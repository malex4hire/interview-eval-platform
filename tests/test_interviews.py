"""Interview creation, authorisation, and tenant isolation."""

from __future__ import annotations

from tests.conftest import INTERVIEW_PAYLOAD, auth, register


def test_admin_creates_interview_with_questions(client, admin):
    response = client.post(
        "/interviews", json=INTERVIEW_PAYLOAD, headers=auth(admin["access_token"])
    )
    assert response.status_code == 201
    body = response.json()

    assert body["title"] == "Backend Round"
    assert body["owner_admin_id"] == admin["user_id"]
    assert len(body["questions"]) == 2
    # Order is assigned server-side from list position, not client-supplied.
    assert [q["order_index"] for q in body["questions"]] == [0, 1]
    assert body["questions"][0]["expected_concepts"] == [
        "idempotency key",
        "retry",
        "duplicate",
        "same result",
    ]


def test_candidate_cannot_create_interview(client, candidate):
    response = client.post(
        "/interviews", json=INTERVIEW_PAYLOAD, headers=auth(candidate["access_token"])
    )
    assert response.status_code == 403


def test_unauthenticated_request_is_rejected(client):
    assert client.post("/interviews", json=INTERVIEW_PAYLOAD).status_code == 401
    assert client.get("/interviews").status_code == 401


def test_interview_is_invisible_to_another_admin(client, admin, other_admin, interview):
    """Cross-tenant reads return 404, not 403.

    403 would confirm the interview exists, leaking one tenant's data to
    another by inference.
    """
    listed = client.get("/interviews", headers=auth(other_admin["access_token"]))
    assert listed.json() == []

    direct = client.get(
        f"/interviews/{interview['id']}", headers=auth(other_admin["access_token"])
    )
    assert direct.status_code == 404


def test_candidate_only_sees_assigned_interviews(
    client, admin, candidate, interview
):
    before = client.get("/me/interviews", headers=auth(candidate["access_token"]))
    assert before.json() == []

    client.post(
        f"/interviews/{interview['id']}/assignments",
        json={"candidate_id": candidate["user_id"]},
        headers=auth(admin["access_token"]),
    )

    after = client.get("/me/interviews", headers=auth(candidate["access_token"]))
    assert [i["id"] for i in after.json()] == [interview["id"]]


def test_candidate_questions_omit_the_rubric(client, assigned_candidate, interview):
    """The answer key must never reach the person being scored."""
    response = client.get(
        f"/me/interviews/{interview['id']}/questions",
        headers=auth(assigned_candidate["access_token"]),
    )
    assert response.status_code == 200
    for question in response.json():
        assert "expected_concepts" not in question


def test_admin_cannot_assign_another_tenants_candidate(
    client, other_admin, candidate, interview
):
    response = client.post(
        f"/interviews/{interview['id']}/assignments",
        json={"candidate_id": candidate["user_id"]},
        headers=auth(other_admin["access_token"]),
    )
    assert response.status_code == 404


def test_duplicate_assignment_conflicts(client, admin, candidate, interview):
    body = {"candidate_id": candidate["user_id"]}
    headers = auth(admin["access_token"])
    assert (
        client.post(
            f"/interviews/{interview['id']}/assignments", json=body, headers=headers
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/interviews/{interview['id']}/assignments", json=body, headers=headers
        ).status_code
        == 409
    )


def test_candidate_registration_requires_a_real_admin(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "orphan@example.com",
            "password": "password-1234",
            "role": "candidate",
            "tenant_admin_id": 9999,
        },
    )
    assert response.status_code == 422


def test_duplicate_email_conflicts(client, admin):
    response = client.post(
        "/auth/register",
        json={
            "email": "admin@example.com",
            "password": "password-1234",
            "role": "org_admin",
        },
    )
    assert response.status_code == 409


def test_login_returns_a_usable_token(client, admin):
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "password-1234"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = client.get("/auth/me", headers=auth(token))
    assert me.json()["email"] == "admin@example.com"


def test_login_rejects_a_wrong_password(client, admin):
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
