"""Canonical JSON and the audit-log hash chain. Stdlib only, no I/O."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, NamedTuple

# The chain's fixed anchor. Every admin's chain starts from this value, so the
# first real entry is verifiable without a synthetic genesis row.
GENESIS_HASH = "0" * 64

# Bump when the hashing scheme changes. Stored on every entry so historical
# chains stay verifiable under their original rules after an algorithm change.
CHAIN_VERSION = 1


def canonical_json(payload: Any) -> str:
    """Sorted keys, no incidental whitespace, UTC datetimes.

    Equal payloads have to serialise identically or the chain raises false
    alarms after a round-trip through the database.
    """

    def default(value: Any) -> Any:
        if isinstance(value, datetime):
            return _iso_utc(value)
        if isinstance(value, set):
            return sorted(value)
        return str(value)

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=default,
    )


def _iso_utc(value: datetime) -> str:
    """UTC with an explicit offset. Naive values are assumed UTC, since that's
    what we store; treating them as local would break chains across hosts."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def compute_entry_hash(
    *,
    prev_hash: str,
    seq: int,
    event_type: str,
    subject_type: str,
    subject_id: int | None,
    actor_id: int | None,
    payload: Mapping[str, Any],
    created_at: datetime,
) -> str:
    """Hash one entry, binding it to its predecessor.

    The envelope (who, what, when, position) goes into the digest along with
    the payload. Anything left out could be edited without detection.
    """
    material = "|".join(
        [
            f"v{CHAIN_VERSION}",
            prev_hash,
            str(seq),
            event_type,
            subject_type,
            "" if subject_id is None else str(subject_id),
            "" if actor_id is None else str(actor_id),
            canonical_json(payload),
            _iso_utc(created_at),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ChainEntry(NamedTuple):
    """What the verifier needs from an audit row. Not the ORM model, so
    verification also works against a JSON export or a fixture."""

    seq: int
    event_type: str
    subject_type: str
    subject_id: int | None
    actor_id: int | None
    payload: Mapping[str, Any]
    created_at: datetime
    prev_hash: str
    entry_hash: str


class ChainVerification(NamedTuple):
    valid: bool
    entries_checked: int
    broken_at_seq: int | None
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "entries_checked": self.entries_checked,
            "broken_at_seq": self.broken_at_seq,
            "reason": self.reason,
        }


def verify_chain(entries: Iterable[ChainEntry]) -> ChainVerification:
    """Walk the chain, report the first break.

    Three failures, kept separate because they mean different things:
    sequence gap = a row was removed, prev_hash mismatch = history re-linked,
    entry_hash mismatch = a row was edited in place.
    """
    expected_prev = GENESIS_HASH
    expected_seq = 1
    checked = 0

    for entry in entries:
        checked += 1

        if entry.seq != expected_seq:
            return ChainVerification(
                valid=False,
                entries_checked=checked,
                broken_at_seq=entry.seq,
                reason=f"sequence gap: expected seq {expected_seq}, found {entry.seq}",
            )

        if entry.prev_hash != expected_prev:
            return ChainVerification(
                valid=False,
                entries_checked=checked,
                broken_at_seq=entry.seq,
                reason="prev_hash does not match the preceding entry's hash",
            )

        recomputed = compute_entry_hash(
            prev_hash=entry.prev_hash,
            seq=entry.seq,
            event_type=entry.event_type,
            subject_type=entry.subject_type,
            subject_id=entry.subject_id,
            actor_id=entry.actor_id,
            payload=entry.payload,
            created_at=entry.created_at,
        )
        if recomputed != entry.entry_hash:
            return ChainVerification(
                valid=False,
                entries_checked=checked,
                broken_at_seq=entry.seq,
                reason="entry content does not match its stored hash",
            )

        expected_prev = entry.entry_hash
        expected_seq += 1

    return ChainVerification(
        valid=True, entries_checked=checked, broken_at_seq=None, reason=None
    )
