"""Interview management, response review, re-evaluation, human review.

All org_admin-only and all tenant-scoped through the repository layer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import repositories as repo
from app.api.deps import Principal, not_found, require_org_admin
from app.core.database import get_db
from app.domain.verdicts import HumanVerdict
from app.models import Interview, InterviewAssignment, Question
from app.schemas import (
    AssignmentCreate,
    AssignmentOut,
    EvaluationOut,
    HumanReviewCreate,
    HumanReviewOut,
    InterviewCreate,
    InterviewOut,
    ReevaluateRequest,
    ResponseWithEvaluations,
)
from app.services.evaluation_service import EvaluationService
from app.services.review_service import (
    AlreadyReviewed,
    ReviewNotPermitted,
    ReviewService,
)

router = APIRouter(tags=["org_admin"])


# ---------------------------------------------------------------------------
# Interview setup
# ---------------------------------------------------------------------------


@router.post(
    "/interviews", response_model=InterviewOut, status_code=status.HTTP_201_CREATED
)
def create_interview(
    payload: InterviewCreate,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> Interview:
    interview = Interview(
        owner_admin_id=principal.id,
        title=payload.title,
        description=payload.description,
    )
    db.add(interview)
    db.flush()

    for index, question in enumerate(payload.questions):
        db.add(
            Question(
                interview_id=interview.id,
                order_index=index,
                text=question.text,
                audio_prompt_url=question.audio_prompt_url,
                expected_concepts=question.expected_concepts,
            )
        )

    db.commit()
    db.refresh(interview)
    return interview


@router.get("/interviews", response_model=list[InterviewOut])
def list_interviews(
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> list[Interview]:
    return repo.list_interviews_for_admin(db, principal.id)


@router.get("/interviews/{interview_id}", response_model=InterviewOut)
def get_interview(
    interview_id: int,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> Interview:
    interview = repo.get_interview_for_admin(db, interview_id, principal.id)
    if interview is None:
        raise not_found("Interview")
    return interview


@router.post(
    "/interviews/{interview_id}/assignments",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
def assign_candidate(
    interview_id: int,
    payload: AssignmentCreate,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> InterviewAssignment:
    if repo.get_interview_for_admin(db, interview_id, principal.id) is None:
        raise not_found("Interview")
    if repo.get_candidate_for_admin(db, payload.candidate_id, principal.id) is None:
        raise not_found("Candidate")

    assignment = InterviewAssignment(
        interview_id=interview_id, candidate_id=payload.candidate_id
    )
    db.add(assignment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate is already assigned to this interview",
        ) from None
    db.refresh(assignment)
    return assignment


# ---------------------------------------------------------------------------
# Responses and evaluations
# ---------------------------------------------------------------------------


@router.get(
    "/interviews/{interview_id}/responses",
    response_model=list[ResponseWithEvaluations],
)
def list_interview_responses(
    interview_id: int,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> list[ResponseWithEvaluations]:
    """Every response with its current evaluation and full version history."""
    if repo.get_interview_for_admin(db, interview_id, principal.id) is None:
        raise not_found("Interview")

    responses = repo.list_responses_for_interview(db, interview_id, principal.id)
    return [
        ResponseWithEvaluations(
            response=r,
            current_evaluation=r.current_evaluation,
            history=list(r.evaluations),
        )
        for r in responses
    ]


@router.post("/responses/{response_id}/reevaluate", response_model=EvaluationOut)
def reevaluate_response(
    response_id: int,
    payload: ReevaluateRequest,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Run the pipeline again, appending a new evaluation version (D-5).

    A reason is mandatory. Re-running an evaluation changes a candidate's
    outcome, so "why" belongs on the permanent record, not in someone's memory.
    """
    response = repo.get_response_for_admin(db, response_id, principal.id)
    if response is None:
        raise not_found("Response")

    service = EvaluationService(db)
    return service.evaluate_response(
        response,
        tenant_admin_id=principal.tenant_admin_id,
        actor_id=principal.id,
        reason=payload.reason,
    )


@router.get("/reviews/pending", response_model=list[EvaluationOut])
def list_pending_reviews(
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """The human-review queue: current evaluations below the threshold."""
    return ReviewService(db).pending_for_admin(principal.id)


@router.post(
    "/evaluations/{evaluation_id}/review",
    response_model=HumanReviewOut,
    status_code=status.HTTP_201_CREATED,
)
def submit_human_review(
    evaluation_id: int,
    payload: HumanReviewCreate,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """Record a final human verdict on a flagged evaluation.

    409 when the evaluation is not flagged (D-7) or has already been reviewed.
    Both are precondition failures on an otherwise valid request, not bad input.
    """
    found = repo.get_evaluation_for_admin(db, evaluation_id, principal.id)
    if found is None:
        raise not_found("Evaluation")
    evaluation, interview_id = found

    try:
        return ReviewService(db).record_review(
            evaluation=evaluation,
            reviewer_id=principal.id,
            tenant_admin_id=principal.tenant_admin_id,
            verdict=HumanVerdict(payload.verdict),
            notes=payload.notes,
            interview_id=interview_id,
        )
    except (ReviewNotPermitted, AlreadyReviewed) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None
