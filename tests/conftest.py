"""Test fixtures.

The database URL and JWT secret are set in the environment *before* any app
module is imported, because settings are cached at first import. A file-backed
SQLite database in a temp dir is used rather than :memory:, so the append-only
triggers and multi-connection behaviour are exercised the same way they are in
a real deployment.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="interview-eval-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["AUDIO_STORAGE_DIR"] = str(_TMP / "audio")
os.environ["CONFIDENCE_THRESHOLD"] = "70"
os.environ["ENVIRONMENT"] = "test"
# Production runs 480,000 iterations. A slow KDF is the point when it's
# guarding a password database; here it just costs ~7 minutes per run.
os.environ["PBKDF2_ITERATIONS"] = "1000"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import (  # noqa: E402
    Base,
    DROP_APPEND_ONLY_SQLITE,
    SessionLocal,
    apply_append_only_guards,
    engine,
)
from app.main import app  # noqa: E402

# Import the models module for its side effect of registering the tables on
# Base.metadata. Aliased because `import app.models` rebinds `app` to the
# package and shadows the FastAPI instance above, which then blows up much
# later as "'module' object is not callable".
from app import models as _models  # noqa: E402,F401

CONFIDENCE_THRESHOLD = 70


@pytest.fixture(autouse=True)
def fresh_database():
    """A clean database per test.

    The file is deleted and rebuilt rather than dropped table by table.
    `drop_all` fails here because foreign keys are enforced (see the PRAGMA in
    app.core.database) and `users.tenant_admin_id` is self-referential, so
    DROP TABLE users raises IntegrityError while rows still reference it.
    Turning the pragma off mid-run doesn't work reliably (SQLite ignores it
    inside a transaction), so deleting the file is simpler and cleaner.

    engine.dispose() runs first so no pooled connection holds the old file
    open, and NOT at teardown, where it would invalidate connections the
    TestClient is still using.
    """
    engine.dispose()
    db_path = get_settings().database_url.replace("sqlite:///", "")
    if os.path.exists(db_path):
        os.remove(db_path)

    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        apply_append_only_guards(connection)
    yield
    # No engine.dispose() here. The TestClient holds pooled connections for
    # the duration of the test, and disposing between tests invalidates them
    # (every test after the first fails). The rebuild above is the isolation.


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --- helpers ---------------------------------------------------------------


def register(client: TestClient, email: str, role: str, tenant_admin_id=None) -> dict:
    payload = {
        "email": email,
        "password": "password-1234",
        "role": role,
        "full_name": email.split("@")[0],
    }
    if tenant_admin_id is not None:
        payload["tenant_admin_id"] = tenant_admin_id
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(client: TestClient) -> dict:
    return register(client, "admin@example.com", "org_admin")


@pytest.fixture
def other_admin(client: TestClient) -> dict:
    return register(client, "other-admin@example.com", "org_admin")


@pytest.fixture
def candidate(client: TestClient, admin: dict) -> dict:
    return register(
        client, "candidate@example.com", "candidate", tenant_admin_id=admin["user_id"]
    )


# Rubrics engineered against app.domain.scoring so each answer lands in a known
# band. Coverage 1.0 and 0.0 are decisive (high confidence); ~0.5 is ambiguous
# (low confidence) and must route to human review.
STRONG_ANSWER = "idempotency key retry duplicate same result"
WEAK_ANSWER = "completely unrelated text about gardening"
AMBIGUOUS_ANSWER = "idempotency key retry"

INTERVIEW_PAYLOAD = {
    "title": "Backend Round",
    "description": "Systems fundamentals",
    "questions": [
        {
            "text": "What is idempotency?",
            "expected_concepts": [
                "idempotency key",
                "retry",
                "duplicate",
                "same result",
            ],
        },
        {
            "text": "Explain indexing.",
            "expected_concepts": ["b-tree", "lookup"],
        },
    ],
}


@pytest.fixture
def interview(client: TestClient, admin: dict) -> dict:
    response = client.post(
        "/interviews", json=INTERVIEW_PAYLOAD, headers=auth(admin["access_token"])
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def assigned_candidate(
    client: TestClient, admin: dict, candidate: dict, interview: dict
) -> dict:
    response = client.post(
        f"/interviews/{interview['id']}/assignments",
        json={"candidate_id": candidate["user_id"]},
        headers=auth(admin["access_token"]),
    )
    assert response.status_code == 201, response.text
    return candidate
