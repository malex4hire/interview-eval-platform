"""Human-in-the-loop review.

Decision D-7: only an evaluation routed to `requires_human_review` can be
overridden. The precondition is checked here, in the action, rather than left
to whichever endpoint happens to call it — a write path that can be reached
without its guard is not a guard.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.verdicts import HumanVerdict, Verdict, can_be_human_reviewed
from app.models import (
    AuditEvent,
    Evaluation,
    HumanReview,
    Interview,
    Question,
    Response,
)
from app.services.audit_service import AuditService


class ReviewNotPermitted(Exception):
    """Raised when an evaluation is not open to human override."""


class AlreadyReviewed(Exception):
    """Raised on a second review of the same evaluation."""


class ReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditService(db)

    def pending_for_admin(self, admin_id: int) -> list[Evaluation]:
        """Current evaluations still awaiting a human decision, tenant-scoped.

        Excludes anything already reviewed. A queue that keeps handled items is
        a queue reviewers stop trusting, and re-reviewing is blocked anyway.
        """
        stmt = (
            select(Evaluation)
            .join(Response, Evaluation.response_id == Response.id)
            .join(Question, Response.question_id == Question.id)
            .join(Interview, Question.interview_id == Interview.id)
            .outerjoin(HumanReview, HumanReview.evaluation_id == Evaluation.id)
            .where(
                Interview.owner_admin_id == admin_id,
                Evaluation.is_current.is_(True),
                Evaluation.verdict == Verdict.REQUIRES_HUMAN_REVIEW.value,
                HumanReview.id.is_(None),
            )
            .order_by(Evaluation.evaluated_at.asc())
        )
        return list(self.db.execute(stmt).scalars())

    def record_review(
        self,
        *,
        evaluation: Evaluation,
        reviewer_id: int,
        tenant_admin_id: int,
        verdict: HumanVerdict,
        notes: str | None,
        interview_id: int,
    ) -> HumanReview:
        if not can_be_human_reviewed(Verdict(evaluation.verdict)):
            raise ReviewNotPermitted(
                f"evaluation {evaluation.id} has verdict '{evaluation.verdict}' and is "
                "not flagged for human review"
            )
        if evaluation.review is not None:
            raise AlreadyReviewed(
                f"evaluation {evaluation.id} was already reviewed at "
                f"{evaluation.review.reviewed_at.isoformat()}"
            )

        review = HumanReview(
            evaluation_id=evaluation.id,
            reviewer_id=reviewer_id,
            verdict=verdict.value,
            notes=notes,
        )
        self.db.add(review)
        self.db.flush()

        # The evaluation row is NOT mutated. The model's verdict and the
        # human's verdict both stand on the record; `final_verdict` composes
        # them for readers.
        self.audit.record(
            tenant_admin_id=tenant_admin_id,
            event_type=AuditEvent.HUMAN_REVIEW_RECORDED.value,
            subject_type="human_review",
            subject_id=review.id,
            actor_id=reviewer_id,
            interview_id=interview_id,
            payload={
                "evaluation_id": evaluation.id,
                "evaluation_version": evaluation.version,
                "response_id": evaluation.response_id,
                "llm_verdict": evaluation.llm_verdict,
                "routed_verdict": evaluation.verdict,
                "llm_confidence": evaluation.confidence,
                "confidence_threshold": evaluation.confidence_threshold,
                "human_verdict": verdict.value,
                "notes": notes,
            },
        )

        self.db.commit()
        self.db.refresh(review)
        return review
