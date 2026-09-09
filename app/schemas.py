"""Pydantic request/response contracts.

These are the public API's shape. Kept separate from the ORM models so the
wire format can stay stable while the schema evolves, and so internal columns
are never leaked by accident.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

ORM = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    role: Literal["org_admin", "candidate"]
    # Required for a candidate: the admin whose tenant they belong to.
    # Ignored for an org_admin, who becomes their own tenant.
    tenant_admin_id: int | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    role: str
    user_id: int
    tenant_admin_id: int


class UserOut(BaseModel):
    model_config = ORM
    id: int
    email: str
    full_name: str | None
    role: str
    tenant_admin_id: int | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Interviews
# ---------------------------------------------------------------------------


class QuestionIn(BaseModel):
    text: str = Field(min_length=1)
    audio_prompt_url: str | None = None
    expected_concepts: list[str] = Field(default_factory=list)

    @field_validator("expected_concepts")
    @classmethod
    def _clean_concepts(cls, value: list[str]) -> list[str]:
        # Blank rubric entries would silently depress every coverage score.
        cleaned = [c.strip() for c in value if c and c.strip()]
        if len(cleaned) != len(set(c.lower() for c in cleaned)):
            raise ValueError("expected_concepts contains duplicates")
        return cleaned


class QuestionOut(BaseModel):
    model_config = ORM
    id: int
    order_index: int
    text: str
    audio_prompt_url: str | None
    expected_concepts: list[str]


class QuestionForCandidate(BaseModel):
    """Candidate-facing view.

    `expected_concepts` is intentionally absent: handing the rubric to the
    person being scored defeats the assessment.
    """

    model_config = ORM
    id: int
    order_index: int
    text: str
    audio_prompt_url: str | None


class InterviewCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    questions: list[QuestionIn] = Field(min_length=1)


class InterviewOut(BaseModel):
    model_config = ORM
    id: int
    title: str
    description: str | None
    owner_admin_id: int
    created_at: datetime
    questions: list[QuestionOut]


class InterviewForCandidate(BaseModel):
    model_config = ORM
    id: int
    title: str
    description: str | None
    created_at: datetime


class AssignmentCreate(BaseModel):
    candidate_id: int


class AssignmentOut(BaseModel):
    model_config = ORM
    id: int
    interview_id: int
    candidate_id: int
    assigned_at: datetime


# ---------------------------------------------------------------------------
# Responses and evaluations
# ---------------------------------------------------------------------------


class HumanReviewOut(BaseModel):
    model_config = ORM
    id: int
    reviewer_id: int
    verdict: str
    notes: str | None
    reviewed_at: datetime


class EvaluationOut(BaseModel):
    """Full evaluation record.

    Exposes both the model's verdict and the routed verdict. A consumer that
    only reads `final_verdict` still gets the right answer; one that needs to
    audit the decision has everything it needs without a second call.
    """

    model_config = ORM
    id: int
    response_id: int
    version: int
    is_current: bool
    provider: str
    model: str
    explanation: str
    matched_concepts: list[str]
    missing_concepts: list[str]
    coverage: float
    confidence: int
    confidence_threshold: int
    llm_verdict: str
    verdict: str
    final_verdict: str
    raw_llm_output: dict[str, Any]
    evaluated_at: datetime
    superseded_at: datetime | None
    reevaluation_reason: str | None
    review: HumanReviewOut | None = None


class CandidateEvaluationOut(BaseModel):
    """Candidate-facing view: feedback, verdict, and the LLM's explanation.

    Omits raw provider output and the rubric's missing concepts — a candidate
    should not be able to reverse-engineer the answer key from their feedback.
    """

    model_config = ORM
    response_id: int
    verdict: str
    final_verdict: str
    confidence: int
    explanation: str
    evaluated_at: datetime
    awaiting_human_review: bool


class ResponseSubmitText(BaseModel):
    text: str | None = None


class ResponseOut(BaseModel):
    model_config = ORM
    id: int
    question_id: int
    candidate_id: int
    text: str | None
    audio_path: str | None
    transcript: str | None
    transcription_source: str | None
    submitted_at: datetime


class SubmissionResult(BaseModel):
    response: ResponseOut
    evaluation: EvaluationOut


class ResponseWithEvaluations(BaseModel):
    response: ResponseOut
    current_evaluation: EvaluationOut | None
    history: list[EvaluationOut]


class ReevaluateRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class HumanReviewCreate(BaseModel):
    verdict: Literal["correct", "incorrect"]
    notes: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditEntryOut(BaseModel):
    model_config = ORM
    id: int
    seq: int
    event_type: str
    subject_type: str
    subject_id: int | None
    actor_id: int | None
    interview_id: int | None
    payload: dict[str, Any]
    created_at: datetime
    prev_hash: str
    entry_hash: str


class ChainVerificationOut(BaseModel):
    valid: bool
    entries_checked: int
    broken_at_seq: int | None
    reason: str | None


class AuditLogExport(BaseModel):
    """The downloadable compliance artefact.

    Carries its own verification result so the export is self-describing: a
    reviewer holding only this JSON can see whether the chain was intact at the
    moment of export, and can re-verify it independently from the hashes.
    """

    interview_id: int | None
    tenant_admin_id: int
    exported_at: datetime
    entry_count: int
    chain_verification: ChainVerificationOut
    entries: list[AuditEntryOut]
