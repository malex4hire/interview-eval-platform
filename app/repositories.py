"""Tenant-scoped data access.

Every read of tenant-owned data goes through here, and every query carries its
scope predicate. Authorisation enforced at the repository rather than in route
handlers means an endpoint cannot forget it — the only way to reach an
interview is to ask for it with an owner or a candidate, and the wrong one
returns None.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Evaluation,
    Interview,
    InterviewAssignment,
    Question,
    Response,
    User,
)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(
        select(User).where(User.email == email.lower())
    ).scalar_one_or_none()


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_candidate_for_admin(
    db: Session, candidate_id: int, admin_id: int
) -> User | None:
    """A candidate is only visible to the admin who owns their tenant."""
    return db.execute(
        select(User).where(
            User.id == candidate_id,
            User.role == "candidate",
            User.tenant_admin_id == admin_id,
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Interviews — admin scope
# ---------------------------------------------------------------------------


def list_interviews_for_admin(db: Session, admin_id: int) -> list[Interview]:
    stmt = (
        select(Interview)
        .where(Interview.owner_admin_id == admin_id)
        .options(selectinload(Interview.questions))
        .order_by(Interview.created_at.desc())
    )
    return list(db.execute(stmt).scalars())


def get_interview_for_admin(
    db: Session, interview_id: int, admin_id: int
) -> Interview | None:
    stmt = (
        select(Interview)
        .where(Interview.id == interview_id, Interview.owner_admin_id == admin_id)
        .options(selectinload(Interview.questions))
    )
    return db.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Interviews — candidate scope (assignment is the only path)
# ---------------------------------------------------------------------------


def list_interviews_for_candidate(db: Session, candidate_id: int) -> list[Interview]:
    stmt = (
        select(Interview)
        .join(InterviewAssignment, InterviewAssignment.interview_id == Interview.id)
        .where(InterviewAssignment.candidate_id == candidate_id)
        .options(selectinload(Interview.questions))
        .order_by(InterviewAssignment.assigned_at.desc())
    )
    return list(db.execute(stmt).scalars())


def get_interview_for_candidate(
    db: Session, interview_id: int, candidate_id: int
) -> Interview | None:
    stmt = (
        select(Interview)
        .join(InterviewAssignment, InterviewAssignment.interview_id == Interview.id)
        .where(
            Interview.id == interview_id,
            InterviewAssignment.candidate_id == candidate_id,
        )
        .options(selectinload(Interview.questions))
    )
    return db.execute(stmt).scalar_one_or_none()


def is_assigned(db: Session, interview_id: int, candidate_id: int) -> bool:
    return (
        db.execute(
            select(InterviewAssignment.id).where(
                InterviewAssignment.interview_id == interview_id,
                InterviewAssignment.candidate_id == candidate_id,
            )
        ).scalar_one_or_none()
        is not None
    )


# ---------------------------------------------------------------------------
# Questions / responses / evaluations
# ---------------------------------------------------------------------------


def get_question_in_interview(
    db: Session, question_id: int, interview_id: int
) -> Question | None:
    return db.execute(
        select(Question).where(
            Question.id == question_id, Question.interview_id == interview_id
        )
    ).scalar_one_or_none()


def get_existing_response(
    db: Session, question_id: int, candidate_id: int
) -> Response | None:
    return db.execute(
        select(Response).where(
            Response.question_id == question_id, Response.candidate_id == candidate_id
        )
    ).scalar_one_or_none()


def list_responses_for_interview(
    db: Session, interview_id: int, admin_id: int
) -> list[Response]:
    stmt = (
        select(Response)
        .join(Question, Response.question_id == Question.id)
        .join(Interview, Question.interview_id == Interview.id)
        .where(Interview.id == interview_id, Interview.owner_admin_id == admin_id)
        .options(selectinload(Response.evaluations).selectinload(Evaluation.review))
        .order_by(Response.submitted_at.asc())
    )
    return list(db.execute(stmt).scalars())


def get_response_for_admin(
    db: Session, response_id: int, admin_id: int
) -> Response | None:
    stmt = (
        select(Response)
        .join(Question, Response.question_id == Question.id)
        .join(Interview, Question.interview_id == Interview.id)
        .where(Response.id == response_id, Interview.owner_admin_id == admin_id)
        .options(selectinload(Response.evaluations).selectinload(Evaluation.review))
    )
    return db.execute(stmt).scalar_one_or_none()


def get_response_for_candidate(
    db: Session, response_id: int, candidate_id: int
) -> Response | None:
    stmt = (
        select(Response)
        .where(Response.id == response_id, Response.candidate_id == candidate_id)
        .options(selectinload(Response.evaluations).selectinload(Evaluation.review))
    )
    return db.execute(stmt).scalar_one_or_none()


def get_evaluation_for_admin(
    db: Session, evaluation_id: int, admin_id: int
) -> tuple[Evaluation, int] | None:
    """Return the evaluation and its interview id, or None if out of scope."""
    stmt = (
        select(Evaluation, Interview.id)
        .join(Response, Evaluation.response_id == Response.id)
        .join(Question, Response.question_id == Question.id)
        .join(Interview, Question.interview_id == Interview.id)
        .where(Evaluation.id == evaluation_id, Interview.owner_admin_id == admin_id)
        .options(selectinload(Evaluation.review))
    )
    row = db.execute(stmt).one_or_none()
    return (row[0], row[1]) if row else None


def get_interview_id_for_response(db: Session, response_id: int) -> int | None:
    return db.execute(
        select(Question.interview_id)
        .join(Response, Response.question_id == Question.id)
        .where(Response.id == response_id)
    ).scalar_one_or_none()
