"""Schema. Kept in one file so the whole data model reads top to bottom.

Tenancy (D-1): no organisations table. The org_admin is the tenant, and
Interview.owner_admin_id is the scoping key every query filters on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.database import Base
from app.domain.verdicts import HumanVerdict, LLMVerdict, Verdict


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt(**kwargs) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), **kwargs)


class UserRole(str, Enum):
    ORG_ADMIN = "org_admin"
    CANDIDATE = "candidate"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base):
    """One table, `role` discriminates.

    tenant_admin_id is an admin's own id, or for a candidate, the admin who
    registered them. On both roles so authz is one column compare either way.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = _dt(default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('org_admin', 'candidate')", name="ck_users_role_valid"
        ),
    )

    def is_admin(self) -> bool:
        return self.role == UserRole.ORG_ADMIN.value


# ---------------------------------------------------------------------------
# Interview setup
# ---------------------------------------------------------------------------


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_admin_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _dt(default=utcnow, nullable=False)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="interview",
        cascade="all, delete-orphan",
        order_by="Question.order_index",
    )
    assignments: Mapped[list["InterviewAssignment"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    audio_prompt_url: Mapped[str | None] = mapped_column(String(1000))
    # The rubric. JSON list of strings; the evaluation pipeline scores against it.
    expected_concepts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    interview: Mapped["Interview"] = relationship(back_populates="questions")

    __table_args__ = (
        UniqueConstraint("interview_id", "order_index", name="uq_question_order"),
    )


class InterviewAssignment(Base):
    """Candidate visibility (decision D-2). No assignment, no access."""

    __tablename__ = "interview_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_at: Mapped[datetime] = _dt(default=utcnow, nullable=False)

    interview: Mapped["Interview"] = relationship(back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("interview_id", "candidate_id", name="uq_assignment"),
    )


# ---------------------------------------------------------------------------
# Candidate submissions
# ---------------------------------------------------------------------------


class Response(Base):
    """A candidate's answer. Text, audio, or both.

    `transcript` is what actually got evaluated — the submitted text, or the
    transcription. Separate from `text` so the audit trail shows what the
    evaluator saw.
    """

    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    text: Mapped[str | None] = mapped_column(Text)
    audio_path: Mapped[str | None] = mapped_column(String(1000))
    transcript: Mapped[str | None] = mapped_column(Text)
    transcription_source: Mapped[str | None] = mapped_column(String(50))
    submitted_at: Mapped[datetime] = _dt(default=utcnow, nullable=False)

    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="response",
        cascade="all, delete-orphan",
        order_by="Evaluation.version",
    )

    __table_args__ = (
        CheckConstraint(
            "text IS NOT NULL OR audio_path IS NOT NULL",
            name="ck_response_has_content",
        ),
        UniqueConstraint("question_id", "candidate_id", name="uq_response_per_question"),
    )

    @property
    def current_evaluation(self) -> "Evaluation | None":
        for evaluation in self.evaluations:
            if evaluation.is_current:
                return evaluation
        return None

    def evaluated_text(self) -> str:
        return self.transcript or self.text or ""


# ---------------------------------------------------------------------------
# Evaluation (versioned — decision D-5)
# ---------------------------------------------------------------------------


class Evaluation(Base):
    """One scoring pass over one response.

    Versioned, never mutated. Re-evaluating appends N+1 and flips is_current;
    older versions stay readable.

    llm_verdict is what the model said; verdict is the state after the
    confidence gate. Two columns because collapsing them erases the model's
    output whenever routing kicks in.
    """

    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    response_id: Mapped[int] = mapped_column(
        ForeignKey("responses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    # Verbatim provider output, kept for audit even though the parsed fields
    # below are what the application reads.
    raw_llm_output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    matched_concepts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_concepts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_verdict: Mapped[str] = mapped_column(String(30), nullable=False)
    verdict: Mapped[str] = mapped_column(String(30), nullable=False)

    evaluated_at: Mapped[datetime] = _dt(default=utcnow, nullable=False)
    superseded_at: Mapped[datetime | None] = _dt()
    reevaluation_reason: Mapped[str | None] = mapped_column(Text)

    response: Mapped["Response"] = relationship(back_populates="evaluations")
    review: Mapped["HumanReview | None"] = relationship(
        back_populates="evaluation", uselist=False, cascade="all, delete-orphan"
    )
    llm_call: Mapped["LLMCall | None"] = relationship(
        back_populates="evaluation", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("response_id", "version", name="uq_evaluation_version"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 100", name="ck_confidence_range"
        ),
        CheckConstraint(
            "verdict IN ('correct', 'incorrect', 'requires_human_review')",
            name="ck_verdict_valid",
        ),
        Index("ix_evaluation_current", "response_id", "is_current"),
    )

    @property
    def requires_human_review(self) -> bool:
        return self.verdict == Verdict.REQUIRES_HUMAN_REVIEW.value

    @property
    def final_verdict(self) -> str:
        """Human decision supersedes the routed verdict."""
        if self.review is not None:
            return self.review.verdict
        return self.verdict


class HumanReview(Base):
    """A reviewer's override of a flagged evaluation. Its own row rather than
    an edit, so both the model's verdict and the human's survive."""

    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    verdict: Mapped[str] = mapped_column(String(30), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = _dt(default=utcnow, nullable=False)

    evaluation: Mapped["Evaluation"] = relationship(back_populates="review")

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('correct', 'incorrect')", name="ck_human_verdict_valid"
        ),
    )


# ---------------------------------------------------------------------------
# Telemetry and compliance
# ---------------------------------------------------------------------------


class LLMCall(Base):
    """Cost and latency ledger, one row per provider call.

    Separate from evaluations: spend is operational, the verdict is a domain
    record, and a failed call that produced no evaluation still lands here.
    """

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluations.id", ondelete="SET NULL"), index=True
    )
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    tenant_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_estimate_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _dt(default=utcnow, nullable=False)

    evaluation: Mapped["Evaluation | None"] = relationship(back_populates="llm_call")


class AuditEvent(str, Enum):
    """Audit scope (decision D-6): LLM evaluations and human reviews only."""

    EVALUATION_CREATED = "evaluation.created"
    EVALUATION_REEVALUATED = "evaluation.reevaluated"
    HUMAN_REVIEW_RECORDED = "human_review.recorded"


class AuditLog(Base):
    """Append-only, hash-chained compliance record.

    seq is per-tenant and gap-free; prev_hash links each entry to the last
    one. Delete a row and the sequence breaks, edit one and its hash breaks,
    re-link and prev_hash breaks. verify_chain() tells the three apart.

    UPDATE and DELETE are blocked in the database itself — see
    app/core/database.py.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_admin_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(Integer)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    interview_id: Mapped[int | None] = mapped_column(Integer, index=True)

    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = _dt(default=utcnow, nullable=False)

    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        UniqueConstraint("tenant_admin_id", "seq", name="uq_audit_seq_per_tenant"),
        Index("ix_audit_tenant_seq", "tenant_admin_id", "seq"),
    )


__all__ = [
    "AuditEvent",
    "AuditLog",
    "Evaluation",
    "HumanReview",
    "HumanVerdict",
    "Interview",
    "InterviewAssignment",
    "LLMCall",
    "LLMVerdict",
    "Question",
    "Response",
    "User",
    "UserRole",
    "Verdict",
]
