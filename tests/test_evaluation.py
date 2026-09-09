"""Response submission, evaluation, confidence routing, re-evaluation."""

from __future__ import annotations

import io

from tests.conftest import (
    AMBIGUOUS_ANSWER,
    CONFIDENCE_THRESHOLD,
    STRONG_ANSWER,
    WEAK_ANSWER,
    auth,
)


def submit(client, candidate, interview, question_index=0, text=None, files=None):
    question_id = interview["questions"][question_index]["id"]
    return client.post(
        f"/me/interviews/{interview['id']}/questions/{question_id}/responses",
        data={"text": text} if text is not None else {},
        files=files,
        headers=auth(candidate["access_token"]),
    )


def test_submission_returns_an_evaluation(client, assigned_candidate, interview):
    response = submit(client, assigned_candidate, interview, text=STRONG_ANSWER)
    assert response.status_code == 201

    body = response.json()
    evaluation = body["evaluation"]

    assert body["response"]["text"] == STRONG_ANSWER
    assert evaluation["version"] == 1
    assert evaluation["is_current"] is True
    assert evaluation["provider"] == "mock"
    assert evaluation["coverage"] == 1.0
    assert evaluation["llm_verdict"] == "correct"
    assert evaluation["verdict"] == "correct"
    assert evaluation["confidence"] >= CONFIDENCE_THRESHOLD
    assert evaluation["confidence_threshold"] == CONFIDENCE_THRESHOLD
    assert evaluation["explanation"]
    assert evaluation["raw_llm_output"]["verdict"] == "correct"


def test_decisively_wrong_answer_is_incorrect_not_flagged(
    client, assigned_candidate, interview
):
    """Confidence measures certainty, not correctness.

    An answer matching nothing in the rubric is an easy call, so it must stand
    as `incorrect` at high confidence rather than being routed to a human.
    """
    response = submit(client, assigned_candidate, interview, text=WEAK_ANSWER)
    evaluation = response.json()["evaluation"]

    assert evaluation["coverage"] == 0.0
    assert evaluation["llm_verdict"] == "incorrect"
    assert evaluation["verdict"] == "incorrect"
    assert evaluation["confidence"] >= CONFIDENCE_THRESHOLD


def test_ambiguous_answer_routes_to_human_review(
    client, assigned_candidate, interview
):
    response = submit(client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER)
    evaluation = response.json()["evaluation"]

    assert evaluation["confidence"] < CONFIDENCE_THRESHOLD
    assert evaluation["verdict"] == "requires_human_review"
    # The model's own verdict survives the routing decision.
    assert evaluation["llm_verdict"] in {"correct", "incorrect"}
    assert evaluation["final_verdict"] == "requires_human_review"


def test_audio_submission_is_transcribed_then_evaluated(
    client, assigned_candidate, interview
):
    files = {"audio": ("answer.txt", io.BytesIO(STRONG_ANSWER.encode()), "text/plain")}
    response = submit(client, assigned_candidate, interview, files=files)
    assert response.status_code == 201

    body = response.json()
    assert body["response"]["audio_path"]
    assert body["response"]["transcript"] == STRONG_ANSWER
    assert body["response"]["transcription_source"].startswith("mock-transcriber")
    assert body["evaluation"]["verdict"] == "correct"


def test_empty_submission_is_rejected(client, assigned_candidate, interview):
    assert submit(client, assigned_candidate, interview, text="   ").status_code == 422


def test_unassigned_candidate_cannot_submit(client, candidate, interview):
    assert submit(client, candidate, interview, text=STRONG_ANSWER).status_code == 404


def test_second_submission_for_same_question_conflicts(
    client, assigned_candidate, interview
):
    assert submit(client, assigned_candidate, interview, text=STRONG_ANSWER).status_code == 201
    assert submit(client, assigned_candidate, interview, text=WEAK_ANSWER).status_code == 409


def test_candidate_reads_own_feedback_without_the_answer_key(
    client, assigned_candidate, interview
):
    submission = submit(
        client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER
    ).json()
    response_id = submission["response"]["id"]

    feedback = client.get(
        f"/me/responses/{response_id}/evaluation",
        headers=auth(assigned_candidate["access_token"]),
    )
    assert feedback.status_code == 200
    body = feedback.json()

    assert body["explanation"]
    assert body["verdict"] == "requires_human_review"
    assert body["awaiting_human_review"] is True
    # Neither the rubric gaps nor the raw provider output are exposed.
    assert "missing_concepts" not in body
    assert "raw_llm_output" not in body


def test_candidate_cannot_read_another_candidates_evaluation(
    client, admin, interview, assigned_candidate
):
    from tests.conftest import register

    submission = submit(
        client, assigned_candidate, interview, text=STRONG_ANSWER
    ).json()

    intruder = register(
        client, "intruder@example.com", "candidate", tenant_admin_id=admin["user_id"]
    )
    response = client.get(
        f"/me/responses/{submission['response']['id']}/evaluation",
        headers=auth(intruder["access_token"]),
    )
    assert response.status_code == 404


def test_reevaluation_appends_a_version_and_supersedes_the_previous(
    client, admin, assigned_candidate, interview
):
    """Decision D-5: history is appended, never overwritten."""
    submission = submit(
        client, assigned_candidate, interview, text=STRONG_ANSWER
    ).json()
    response_id = submission["response"]["id"]

    again = client.post(
        f"/responses/{response_id}/reevaluate",
        json={"reason": "model upgrade"},
        headers=auth(admin["access_token"]),
    )
    assert again.status_code == 200
    assert again.json()["version"] == 2
    assert again.json()["reevaluation_reason"] == "model upgrade"

    listing = client.get(
        f"/interviews/{interview['id']}/responses",
        headers=auth(admin["access_token"]),
    ).json()
    record = next(r for r in listing if r["response"]["id"] == response_id)

    assert len(record["history"]) == 2
    assert record["current_evaluation"]["version"] == 2
    v1 = next(e for e in record["history"] if e["version"] == 1)
    assert v1["is_current"] is False
    assert v1["superseded_at"] is not None


def test_reevaluation_requires_a_reason(client, admin, assigned_candidate, interview):
    submission = submit(
        client, assigned_candidate, interview, text=STRONG_ANSWER
    ).json()
    response = client.post(
        f"/responses/{submission['response']['id']}/reevaluate",
        json={},
        headers=auth(admin["access_token"]),
    )
    assert response.status_code == 422


def test_llm_call_ledger_records_cost_for_every_evaluation(
    client, db, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=STRONG_ANSWER)

    from app.models import LLMCall

    calls = db.query(LLMCall).all()
    assert len(calls) == 1
    call = calls[0]
    assert call.succeeded is True
    assert call.prompt
    assert call.prompt_tokens > 0
    assert call.cost_estimate_usd > 0
    assert call.evaluation_id is not None
