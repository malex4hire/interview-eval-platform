"""The human-in-the-loop gate (decision D-7)."""

from __future__ import annotations

from tests.conftest import AMBIGUOUS_ANSWER, STRONG_ANSWER, auth
from tests.test_evaluation import submit


def test_flagged_evaluation_appears_in_the_review_queue(
    client, admin, assigned_candidate, interview
):
    submit(client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER)

    queue = client.get("/reviews/pending", headers=auth(admin["access_token"]))
    assert queue.status_code == 200
    assert len(queue.json()) == 1
    assert queue.json()[0]["verdict"] == "requires_human_review"


def test_human_verdict_supersedes_without_erasing_the_model_verdict(
    client, admin, assigned_candidate, interview
):
    """Both verdicts stay on the record.

    Overwriting the evaluation would make the audit trail unable to answer
    "what did the model say before the human intervened?".
    """
    submission = submit(
        client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER
    ).json()
    evaluation_id = submission["evaluation"]["id"]
    model_verdict = submission["evaluation"]["llm_verdict"]

    review = client.post(
        f"/evaluations/{evaluation_id}/review",
        json={"verdict": "correct", "notes": "Partial but acceptable for the level."},
        headers=auth(admin["access_token"]),
    )
    assert review.status_code == 201
    assert review.json()["verdict"] == "correct"

    listing = client.get(
        f"/interviews/{interview['id']}/responses",
        headers=auth(admin["access_token"]),
    ).json()
    evaluation = listing[0]["current_evaluation"]

    assert evaluation["verdict"] == "requires_human_review"  # routed state, unchanged
    assert evaluation["llm_verdict"] == model_verdict  # model's call, preserved
    assert evaluation["final_verdict"] == "correct"  # human's call, authoritative
    assert evaluation["review"]["notes"]


def test_high_confidence_evaluation_cannot_be_overridden(
    client, admin, assigned_candidate, interview
):
    """Without this guard the confidence threshold is decorative."""
    submission = submit(
        client, assigned_candidate, interview, text=STRONG_ANSWER
    ).json()

    response = client.post(
        f"/evaluations/{submission['evaluation']['id']}/review",
        json={"verdict": "incorrect"},
        headers=auth(admin["access_token"]),
    )
    assert response.status_code == 409
    assert "not flagged" in response.json()["detail"]


def test_double_review_conflicts(client, admin, assigned_candidate, interview):
    submission = submit(
        client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER
    ).json()
    evaluation_id = submission["evaluation"]["id"]
    body = {"verdict": "correct"}
    headers = auth(admin["access_token"])

    assert (
        client.post(
            f"/evaluations/{evaluation_id}/review", json=body, headers=headers
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/evaluations/{evaluation_id}/review", json=body, headers=headers
        ).status_code
        == 409
    )


def test_another_tenant_cannot_review(
    client, other_admin, assigned_candidate, interview
):
    submission = submit(
        client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER
    ).json()
    response = client.post(
        f"/evaluations/{submission['evaluation']['id']}/review",
        json={"verdict": "correct"},
        headers=auth(other_admin["access_token"]),
    )
    assert response.status_code == 404


def test_candidate_cannot_review(client, assigned_candidate, interview):
    submission = submit(
        client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER
    ).json()
    response = client.post(
        f"/evaluations/{submission['evaluation']['id']}/review",
        json={"verdict": "correct"},
        headers=auth(assigned_candidate["access_token"]),
    )
    assert response.status_code == 403


def test_reviewed_evaluation_leaves_the_queue(
    client, admin, assigned_candidate, interview
):
    submission = submit(
        client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER
    ).json()
    headers = auth(admin["access_token"])

    assert len(client.get("/reviews/pending", headers=headers).json()) == 1

    client.post(
        f"/evaluations/{submission['evaluation']['id']}/review",
        json={"verdict": "incorrect"},
        headers=headers,
    )

    assert client.get("/reviews/pending", headers=headers).json() == []


def test_reevaluation_returns_a_response_to_the_queue(
    client, admin, assigned_candidate, interview
):
    """A new evaluation version is a new decision, unreviewed by definition.

    The old version's review stays attached to the version it judged; it must
    not silently carry forward and mark the new verdict as already handled.
    """
    submission = submit(
        client, assigned_candidate, interview, text=AMBIGUOUS_ANSWER
    ).json()
    headers = auth(admin["access_token"])

    client.post(
        f"/evaluations/{submission['evaluation']['id']}/review",
        json={"verdict": "correct"},
        headers=headers,
    )
    assert client.get("/reviews/pending", headers=headers).json() == []

    client.post(
        f"/responses/{submission['response']['id']}/reevaluate",
        json={"reason": "threshold recalibrated"},
        headers=headers,
    )

    queue = client.get("/reviews/pending", headers=headers).json()
    assert len(queue) == 1
    assert queue[0]["version"] == 2
    assert queue[0]["review"] is None
