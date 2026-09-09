"""Append-only, hash-chained compliance log.

Every write goes through record(). Sequence number and hash link are derived
inside one transaction, holding a row lock on the tenant's last entry, so two
concurrent evaluations can't both claim seq N and fork the chain.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.hashing import (
    GENESIS_HASH,
    ChainEntry,
    ChainVerification,
    compute_entry_hash,
    verify_chain,
)
from app.models import AuditLog


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # -- write ---------------------------------------------------------------

    def record(
        self,
        *,
        tenant_admin_id: int,
        event_type: str,
        subject_type: str,
        subject_id: int | None,
        actor_id: int | None,
        payload: Mapping[str, Any],
        interview_id: int | None = None,
    ) -> AuditLog:
        """Append one entry. Caller owns the transaction.

        No commit here — the entry and the change it describes have to land
        together, or a crash between them leaves the log describing something
        that never happened.
        """
        tail = self._tail(tenant_admin_id)
        seq = 1 if tail is None else tail.seq + 1
        prev_hash = GENESIS_HASH if tail is None else tail.entry_hash
        created_at = datetime.now(timezone.utc)
        payload = dict(payload)

        entry_hash = compute_entry_hash(
            prev_hash=prev_hash,
            seq=seq,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
        )

        entry = AuditLog(
            tenant_admin_id=tenant_admin_id,
            seq=seq,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            actor_id=actor_id,
            interview_id=interview_id,
            payload=payload,
            created_at=created_at,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self.db.add(entry)
        # Flush so the UNIQUE(tenant_admin_id, seq) constraint rejects a
        # concurrent duplicate now, inside the caller's transaction, rather
        # than at commit time when the domain write has already been staged.
        self.db.flush()
        return entry

    def _tail(self, tenant_admin_id: int) -> AuditLog | None:
        """Last entry for a tenant, locked where the backend supports it.

        SQLite has no SELECT ... FOR UPDATE but serialises writers anyway, so
        the unlocked read is fine there. Checking the dialect rather than
        catching the error, because on Postgres a failed statement aborts the
        caller's transaction and there'd be nothing left to fall back to.
        """
        stmt = (
            select(AuditLog)
            .where(AuditLog.tenant_admin_id == tenant_admin_id)
            .order_by(AuditLog.seq.desc())
            .limit(1)
        )
        if self.db.bind is not None and self.db.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    # -- read ----------------------------------------------------------------

    def entries_for_tenant(self, tenant_admin_id: int) -> list[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(AuditLog.tenant_admin_id == tenant_admin_id)
            .order_by(AuditLog.seq.asc())
        )
        return list(self.db.execute(stmt).scalars())

    def entries_for_interview(
        self, tenant_admin_id: int, interview_id: int
    ) -> list[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.tenant_admin_id == tenant_admin_id,
                AuditLog.interview_id == interview_id,
            )
            .order_by(AuditLog.seq.asc())
        )
        return list(self.db.execute(stmt).scalars())

    # -- verify --------------------------------------------------------------

    def verify(self, tenant_admin_id: int) -> ChainVerification:
        """Verify a tenant's whole chain.

        Always the full chain, never a filtered subset — a per-interview slice
        has legitimate gaps and would report tampering that didn't happen.
        That's why scoped exports carry the tenant-wide result.
        """
        return verify_chain(
            ChainEntry(
                seq=row.seq,
                event_type=row.event_type,
                subject_type=row.subject_type,
                subject_id=row.subject_id,
                actor_id=row.actor_id,
                payload=row.payload,
                created_at=row.created_at,
                prev_hash=row.prev_hash,
                entry_hash=row.entry_hash,
            )
            for row in self.entries_for_tenant(tenant_admin_id)
        )
