"""Audit log integrity.

The important tests here are the negative ones. A chain that verifies clean
data proves nothing; the guard has to be shown catching a tamper, a deletion,
and a re-link, or it is an assumption rather than a control.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.database import DROP_APPEND_ONLY_SQLITE, engine
from app.domain.hashing import (
    GENESIS_HASH,
    ChainEntry,
    canonical_json,
    compute_entry_hash,
    verify_chain,
)
from tests.conftest import AMBIGUOUS_ANSWER, STRONG_ANSWER, auth
from tests.test_evaluation import submit


# ---------------------------------------------------------------------------
# Chain construction through the API
# ---------------------------------------------------------------------------


def test_every_evaluation_appends_a_linked_entry(
    client, admin, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)
    submit(client, assigned_candidate, interview, question_index=1, text="b-tree lookup")

    export = client.get("/audit-log", headers=auth(admin["access_token"])).json()

    assert export["entry_count"] == 2
    assert export["chain_verification"]["valid"] is True
    assert [e["seq"] for e in export["entries"]] == [1, 2]
    assert export["entries"][0]["prev_hash"] == GENESIS_HASH
    assert export["entries"][1]["prev_hash"] == export["entries"][0]["entry_hash"]
    assert all(e["event_type"] == "evaluation.created" for e in export["entries"])


def test_audit_payload_captures_the_full_llm_interaction(
    client, admin, assigned_candidate, interview
):
    """What compliance actually needs: prompt, raw output, threshold, verdicts."""
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)

    entry = client.get(
        "/audit-log", headers=auth(admin["access_token"])
    ).json()["entries"][0]
    payload = entry["payload"]

    for field in (
        "prompt",
        "raw_llm_output",
        "confidence",
        "confidence_threshold",
        "llm_verdict",
        "verdict",
        "candidate_id",
        "provider",
        "model",
    ):
        assert field in payload, f"audit payload is missing {field}"
    assert entry["actor_id"] == assigned_candidate["user_id"]
    assert entry["created_at"]


def test_human_review_and_reevaluation_are_both_audited(
    client, admin, assigned_candidate, interview
):
    submission = submit(
        client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER
    ).json()
    headers = auth(admin["access_token"])

    client.post(
        f"/evaluations/{submission['evaluation']['id']}/review",
        json={"verdict": "correct", "notes": "acceptable"},
        headers=headers,
    )
    client.post(
        f"/responses/{submission['response']['id']}/reevaluate",
        json={"reason": "rubric updated"},
        headers=headers,
    )

    export = client.get("/audit-log", headers=headers).json()
    assert [e["event_type"] for e in export["entries"]] == [
        "evaluation.created",
        "human_review.recorded",
        "evaluation.reevaluated",
    ]
    assert export["chain_verification"]["valid"] is True

    review_entry = export["entries"][1]
    assert review_entry["payload"]["human_verdict"] == "correct"
    assert review_entry["payload"]["llm_verdict"] in {"correct", "incorrect"}
    assert review_entry["actor_id"] == admin["user_id"]


def test_interview_export_is_scoped_but_verifies_the_whole_chain(
    client, admin, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)

    export = client.get(
        f"/interviews/{interview['id']}/audit-log", headers=auth(admin["access_token"])
    ).json()

    assert export["interview_id"] == interview["id"]
    assert export["chain_verification"]["valid"] is True
    assert all(e["interview_id"] == interview["id"] for e in export["entries"])


def test_tenants_have_independent_chains(
    client, admin, other_admin, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)

    other = client.get("/audit-log", headers=auth(other_admin["access_token"])).json()
    assert other["entry_count"] == 0
    assert other["chain_verification"]["valid"] is True


def test_candidate_cannot_read_the_audit_log(client, assigned_candidate):
    response = client.get(
        "/audit-log", headers=auth(assigned_candidate["access_token"])
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Append-only enforcement at the database level
# ---------------------------------------------------------------------------


def test_database_rejects_update_of_an_audit_row(
    client, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)

    with pytest.raises(Exception) as excinfo:
        with engine.begin() as connection:
            connection.execute(text("UPDATE audit_log SET payload = '{}' WHERE seq = 1"))
    assert "append-only" in str(excinfo.value)


def test_database_rejects_delete_of_an_audit_row(
    client, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)

    with pytest.raises(Exception) as excinfo:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM audit_log WHERE seq = 1"))
    assert "append-only" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Negative tests: the verifier must actually catch tampering
# ---------------------------------------------------------------------------


def _drop_guards(connection) -> None:
    """Simulate an attacker with direct database access.

    The triggers stop the application; they do not stop someone holding
    credentials. The hash chain is the control that survives that, so it has to
    be tested with the triggers out of the way.
    """
    for statement in DROP_APPEND_ONLY_SQLITE:
        connection.execute(text(statement))


def test_verifier_detects_an_edited_payload(
    client, admin, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)
    submit(client, assigned_candidate, interview, question_index=1, text="b-tree lookup")
    headers = auth(admin["access_token"])
    assert client.get("/audit-log/verify", headers=headers).json()["valid"] is True

    with engine.begin() as connection:
        _drop_guards(connection)
        connection.execute(
            text("UPDATE audit_log SET payload = :payload WHERE seq = 1").bindparams(
                payload=json.dumps({"verdict": "correct", "confidence": 100})
            )
        )

    verification = client.get("/audit-log/verify", headers=headers).json()
    assert verification["valid"] is False
    assert verification["broken_at_seq"] == 1
    assert "does not match its stored hash" in verification["reason"]


def test_verifier_detects_a_deleted_entry(
    client, admin, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)
    submit(client, assigned_candidate, interview, question_index=1, text="b-tree lookup")
    headers = auth(admin["access_token"])

    with engine.begin() as connection:
        _drop_guards(connection)
        connection.execute(text("DELETE FROM audit_log WHERE seq = 1"))

    verification = client.get("/audit-log/verify", headers=headers).json()
    assert verification["valid"] is False
    assert "sequence gap" in verification["reason"]


def test_verifier_detects_a_rewritten_hash(
    client, admin, assigned_candidate, interview
):
    """The subtle attack: edit the row AND recompute its hash.

    Detected because the following entry's prev_hash still points at the
    original digest — rewriting one entry requires rewriting every entry after
    it, which is the property the chain exists to provide.
    """
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)
    submit(client, assigned_candidate, interview, question_index=1, text="b-tree lookup")
    headers = auth(admin["access_token"])

    export = client.get("/audit-log", headers=headers).json()
    first = export["entries"][0]
    forged_payload = {"verdict": "correct", "confidence": 100}
    forged_hash = compute_entry_hash(
        prev_hash=GENESIS_HASH,
        seq=1,
        event_type=first["event_type"],
        subject_type=first["subject_type"],
        subject_id=first["subject_id"],
        actor_id=first["actor_id"],
        payload=forged_payload,
        created_at=datetime.fromisoformat(first["created_at"]),
    )

    with engine.begin() as connection:
        _drop_guards(connection)
        connection.execute(
            text(
                "UPDATE audit_log SET payload = :payload, entry_hash = :entry_hash "
                "WHERE seq = 1"
            ).bindparams(
                payload=json.dumps(forged_payload), entry_hash=forged_hash
            )
        )

    verification = client.get("/audit-log/verify", headers=headers).json()
    assert verification["valid"] is False
    assert verification["broken_at_seq"] == 2
    assert "prev_hash" in verification["reason"]


# ---------------------------------------------------------------------------
# Pure hashing unit tests (no database, no framework)
# ---------------------------------------------------------------------------


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_hash_changes_when_any_field_changes():
    common = dict(
        prev_hash=GENESIS_HASH,
        seq=1,
        event_type="evaluation.created",
        subject_type="evaluation",
        subject_id=1,
        actor_id=2,
        payload={"verdict": "correct"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    baseline = compute_entry_hash(**common)

    assert compute_entry_hash(**{**common, "seq": 2}) != baseline
    assert compute_entry_hash(**{**common, "actor_id": 3}) != baseline
    assert (
        compute_entry_hash(**{**common, "payload": {"verdict": "incorrect"}})
        != baseline
    )
    assert (
        compute_entry_hash(
            **{**common, "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc)}
        )
        != baseline
    )


def test_verify_chain_accepts_an_empty_chain():
    result = verify_chain([])
    assert result.valid is True
    assert result.entries_checked == 0


def test_verify_chain_rejects_a_broken_link():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = {"k": "v"}
    first_hash = compute_entry_hash(
        prev_hash=GENESIS_HASH,
        seq=1,
        event_type="e",
        subject_type="s",
        subject_id=1,
        actor_id=1,
        payload=payload,
        created_at=created,
    )
    entries = [
        ChainEntry(1, "e", "s", 1, 1, payload, created, GENESIS_HASH, first_hash),
        # prev_hash points nowhere real.
        ChainEntry(2, "e", "s", 2, 1, payload, created, "f" * 64, "0" * 64),
    ]
    result = verify_chain(entries)
    assert result.valid is False
    assert result.broken_at_seq == 2
