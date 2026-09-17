"""Demo data.

Two admins (proving tenant isolation is real, not asserted), two interviews,
four candidates, and response sets chosen to exercise every branch of the
verdict router: a clear pass, a clear fail, an ambiguous answer that routes to
human review, and an audio submission that goes through the mock transcriber.

    python -m scripts.seed [--reset] [--if-empty]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    Evaluation,
    Interview,
    InterviewAssignment,
    Question,
    Response,
    User,
    UserRole,
)
from app.services.evaluation_service import EvaluationService

DEFAULT_PASSWORD = "demo-password-123"


# --- interview definitions -------------------------------------------------

BACKEND_INTERVIEW = {
    "title": "Senior Backend Engineer — Systems Round",
    "description": "Distributed systems fundamentals and API design.",
    "questions": [
        {
            "text": "What is idempotency and why does it matter for a payments API?",
            "expected_concepts": [
                "idempotency key",
                "retry",
                "duplicate",
                "same result",
                "at least once",
            ],
        },
        {
            "text": "Explain database indexing and one cost of adding an index.",
            "expected_concepts": ["b-tree", "lookup", "write overhead", "storage"],
        },
        {
            "text": "How would you keep two services consistent without distributed transactions?",
            "expected_concepts": [
                "outbox",
                "saga",
                "eventual consistency",
                "compensating",
            ],
        },
    ],
}

PLATFORM_INTERVIEW = {
    "title": "AI Platform Engineer — Evaluation Round",
    "description": "LLM evaluation, guardrails, and observability.",
    "questions": [
        {
            "text": "How would you evaluate an LLM-based grading pipeline before trusting it?",
            "expected_concepts": [
                "golden dataset",
                "inter rater agreement",
                "confidence",
                "human review",
            ],
        },
        {
            "text": "What do you log for every LLM call in a regulated environment?",
            "expected_concepts": ["prompt", "model version", "timestamp", "cost"],
        },
    ],
}


# --- answers, chosen to hit specific verdict branches ----------------------

ANSWERS = {
    # 5/5 concepts -> high coverage, decisive, passes the gate as `correct`.
    "idempotency_strong": (
        "Idempotency means repeating the same operation produces the same result. "
        "You send an idempotency key with the request so a retry after a timeout "
        "is recognised as a duplicate rather than charging twice. Delivery is at "
        "least once, so the server must dedupe on that key."
    ),
    # 0/5 concepts -> decisively wrong, high confidence, stands as `incorrect`.
    # This is the case that proves confidence models certainty, not correctness.
    "idempotency_wrong": (
        "It is a way of making the database faster by caching rows in memory so "
        "the application does not have to read from disk every time."
    ),
    # ~2/4 -> ambiguous, low confidence, routed to requires_human_review.
    "indexing_partial": (
        "An index is usually a b-tree that makes a lookup much faster than a "
        "full scan. There is a downside but I do not remember the details."
    ),
    # 2/4 -> also ambiguous; a second item for the review queue.
    "consistency_partial": (
        "You would use eventual consistency between the services and have each "
        "one publish what happened, with a compensating step if something goes "
        "wrong. I am not sure what the rest of the pattern is called."
    ),
    # 4/4 -> strong pass on the platform interview.
    "eval_strong": (
        "Build a golden dataset labelled by humans, measure inter rater agreement "
        "between the model and the labellers, calibrate the confidence score, and "
        "route anything below threshold to human review until the agreement rate "
        "justifies autonomy."
    ),
    # Delivered as an "audio" upload; the mock transcriber reads it back.
    "logging_audio": (
        "For every call I would log the prompt, the model version, the timestamp, "
        "and the cost, all tied to a trace id so the whole evaluation is "
        "reconstructable later."
    ),
}


def _already_seeded() -> bool:
    """True when the database already carries at least one evaluation.

    Checked rather than assumed, so re-running the demo command is a no-op on
    existing data instead of a second seed pass or a reset. Any error reading
    the table (most often: the schema does not exist yet) means "not seeded".
    """
    db = SessionLocal()
    try:
        return db.execute(select(Evaluation.id).limit(1)).scalar_one_or_none() is not None
    except SQLAlchemyError:
        return False
    finally:
        db.close()


def _get_or_create_user(db, *, email, name, role, tenant_admin_id=None) -> User:
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        return existing
    user = User(
        email=email,
        password_hash=hash_password(DEFAULT_PASSWORD),
        full_name=name,
        role=role,
        tenant_admin_id=tenant_admin_id,
    )
    db.add(user)
    db.flush()
    if role == UserRole.ORG_ADMIN.value:
        user.tenant_admin_id = user.id
    db.flush()
    return user


def _create_interview(db, admin: User, spec: dict) -> Interview:
    interview = Interview(
        owner_admin_id=admin.id, title=spec["title"], description=spec["description"]
    )
    db.add(interview)
    db.flush()
    for index, question in enumerate(spec["questions"]):
        db.add(
            Question(
                interview_id=interview.id,
                order_index=index,
                text=question["text"],
                expected_concepts=question["expected_concepts"],
            )
        )
    db.flush()
    db.refresh(interview)
    return interview


def _assign(db, interview: Interview, candidate: User) -> None:
    db.add(
        InterviewAssignment(interview_id=interview.id, candidate_id=candidate.id)
    )
    db.flush()


def _write_audio_fixture(name: str, transcript: str) -> str:
    """Write a UTF-8 'audio' file the MockTranscriber reads back verbatim.

    Shipping real audio would require an STT dependency the brief scopes out;
    this keeps the audio path genuinely exercised end to end.
    """
    settings = get_settings()
    path = Path(settings.audio_storage_dir) / name
    path.write_text(transcript, encoding="utf-8")
    return str(path)


def _submit(db, *, question: Question, candidate: User, admin: User, text=None, audio_path=None):
    response = Response(
        question_id=question.id,
        candidate_id=candidate.id,
        text=text,
        audio_path=audio_path,
    )
    db.add(response)
    db.flush()
    evaluation = EvaluationService(db).evaluate_response(
        response, tenant_admin_id=admin.id, actor_id=candidate.id
    )
    return response, evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo data")
    parser.add_argument(
        "--reset", action="store_true", help="drop and recreate the schema first"
    )
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help=(
            "do nothing if the database already holds evaluations. Lets the "
            "one-command entry point be re-run without ever overwriting a "
            "verdict that has already been recorded and audited."
        ),
    )
    args = parser.parse_args()

    if args.if_empty and args.reset:
        print("--if-empty and --reset are contradictory; pick one.")
        return 2

    if args.if_empty and _already_seeded():
        print("Database already holds evaluations; leaving them untouched.")
        return 0

    if args.reset:
        import subprocess

        subprocess.run(
            [sys.executable, "-m", "scripts.init_db", "--drop"], check=True
        )

    settings = get_settings()
    db = SessionLocal()
    try:
        # -- two tenants, so isolation can actually be demonstrated ----------
        admin_a = _get_or_create_user(
            db,
            email="admin.acme@example.com",
            name="Dana Reyes (Acme)",
            role=UserRole.ORG_ADMIN.value,
        )
        admin_b = _get_or_create_user(
            db,
            email="admin.globex@example.com",
            name="Sam Okafor (Globex)",
            role=UserRole.ORG_ADMIN.value,
        )
        db.commit()

        candidates_a = [
            _get_or_create_user(
                db,
                email=email,
                name=name,
                role=UserRole.CANDIDATE.value,
                tenant_admin_id=admin_a.id,
            )
            for email, name in [
                ("cand.jordan@example.com", "Jordan Blake"),
                ("cand.priya@example.com", "Priya Nair"),
                ("cand.luis@example.com", "Luis Ferreira"),
            ]
        ]
        candidate_b = _get_or_create_user(
            db,
            email="cand.wei@example.com",
            name="Wei Zhang",
            role=UserRole.CANDIDATE.value,
            tenant_admin_id=admin_b.id,
        )
        db.commit()

        backend = _create_interview(db, admin_a, BACKEND_INTERVIEW)
        platform = _create_interview(db, admin_b, PLATFORM_INTERVIEW)

        for candidate in candidates_a:
            _assign(db, backend, candidate)
        _assign(db, platform, candidate_b)
        db.commit()

        q_idem, q_index, q_consistency = backend.questions
        q_eval, q_logging = platform.questions

        results = []
        results.append(
            ("strong pass",)
            + _submit(
                db,
                question=q_idem,
                candidate=candidates_a[0],
                admin=admin_a,
                text=ANSWERS["idempotency_strong"],
            )
        )
        results.append(
            ("confident miss",)
            + _submit(
                db,
                question=q_idem,
                candidate=candidates_a[1],
                admin=admin_a,
                text=ANSWERS["idempotency_wrong"],
            )
        )
        results.append(
            ("ambiguous -> review",)
            + _submit(
                db,
                question=q_index,
                candidate=candidates_a[0],
                admin=admin_a,
                text=ANSWERS["indexing_partial"],
            )
        )
        results.append(
            ("ambiguous -> review",)
            + _submit(
                db,
                question=q_consistency,
                candidate=candidates_a[2],
                admin=admin_a,
                text=ANSWERS["consistency_partial"],
            )
        )
        results.append(
            ("strong pass (tenant B)",)
            + _submit(
                db,
                question=q_eval,
                candidate=candidate_b,
                admin=admin_b,
                text=ANSWERS["eval_strong"],
            )
        )
        audio_path = _write_audio_fixture(
            "seed_logging_answer.txt", ANSWERS["logging_audio"]
        )
        results.append(
            ("audio submission",)
            + _submit(
                db,
                question=q_logging,
                candidate=candidate_b,
                admin=admin_b,
                audio_path=audio_path,
            )
        )

        db.commit()

        print("\nSeed complete.")
        print(f"  confidence threshold : {settings.confidence_threshold}")
        print(f"  password (all users) : {DEFAULT_PASSWORD}")
        print(f"\n  Tenant A admin : {admin_a.email} (id={admin_a.id})")
        print(f"  Tenant B admin : {admin_b.email} (id={admin_b.id})")
        print("\n  Evaluations:")
        for label, _response, evaluation in results:
            print(
                f"    {label:<24} conf={evaluation.confidence:>3} "
                f"llm={evaluation.llm_verdict:<9} routed={evaluation.verdict}"
            )
        print(
            "\n  Log in as an admin and GET /reviews/pending to see the "
            "human-review queue.\n"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
