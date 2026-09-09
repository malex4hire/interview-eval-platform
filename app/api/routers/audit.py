"""Audit log export and chain verification."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import repositories as repo
from app.api.deps import Principal, not_found, require_org_admin
from app.core.database import get_db
from app.schemas import AuditLogExport, ChainVerificationOut
from app.services.audit_service import AuditService

router = APIRouter(tags=["audit"])


@router.get("/interviews/{interview_id}/audit-log", response_model=AuditLogExport)
def download_interview_audit_log(
    interview_id: int,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> AuditLogExport:
    """Full JSON export for one interview (decision D-8).

    The entries are filtered to the interview; the verification result is
    computed over the tenant's entire chain. A filtered slice has legitimate
    sequence gaps, so verifying the slice alone would report tampering that did
    not happen — the export says so by carrying both.
    """
    if repo.get_interview_for_admin(db, interview_id, principal.id) is None:
        raise not_found("Interview")

    service = AuditService(db)
    entries = service.entries_for_interview(principal.tenant_admin_id, interview_id)
    verification = service.verify(principal.tenant_admin_id)

    return AuditLogExport(
        interview_id=interview_id,
        tenant_admin_id=principal.tenant_admin_id,
        exported_at=datetime.now(timezone.utc),
        entry_count=len(entries),
        chain_verification=ChainVerificationOut(**verification.as_dict()),
        entries=entries,
    )


@router.get("/audit-log", response_model=AuditLogExport)
def download_full_audit_log(
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> AuditLogExport:
    """The tenant's complete chain, verifiable end to end."""
    service = AuditService(db)
    entries = service.entries_for_tenant(principal.tenant_admin_id)
    verification = service.verify(principal.tenant_admin_id)

    return AuditLogExport(
        interview_id=None,
        tenant_admin_id=principal.tenant_admin_id,
        exported_at=datetime.now(timezone.utc),
        entry_count=len(entries),
        chain_verification=ChainVerificationOut(**verification.as_dict()),
        entries=entries,
    )


@router.get("/audit-log/verify", response_model=ChainVerificationOut)
def verify_audit_chain(
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> ChainVerificationOut:
    """Standalone integrity check — cheap enough to run on a schedule."""
    verification = AuditService(db).verify(principal.tenant_admin_id)
    return ChainVerificationOut(**verification.as_dict())
