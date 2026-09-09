"""Candidate workflow: see assigned interviews, answer, read own feedback."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app import repositories as repo
from app.api.deps import Principal, not_found, require_candidate
from app.core.database import get_db
from app.models import Interview, Response
from app.schemas import (
    CandidateEvaluationOut,
    InterviewForCandidate,
    QuestionForCandidate,
    SubmissionResult,
)
from app.services.evaluation_service import EvaluationService
from app.services.storage import UploadRejected, store_audio

router = APIRouter(tags=["candidate"])


@router.get("/me/interviews", response_model=list[InterviewForCandidate])
def my_interviews(
    principal: Principal = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> list[Interview]:
    """Assigned interviews only (D-2)."""
    return repo.list_interviews_for_candidate(db, principal.id)


@router.get(
    "/me/interviews/{interview_id}/questions",
    response_model=list[QuestionForCandidate],
)
def my_interview_questions(
    interview_id: int,
    principal: Principal = Depends(require_candidate),
    db: Session = Depends(get_db),
):
    """Questions without the rubric — see QuestionForCandidate."""
    interview = repo.get_interview_for_candidate(db, interview_id, principal.id)
    if interview is None:
        raise not_found("Interview")
    return interview.questions


@router.post(
    "/me/interviews/{interview_id}/questions/{question_id}/responses",
    response_model=SubmissionResult,
    status_code=status.HTTP_201_CREATED,
)
async def submit_response(
    interview_id: int,
    question_id: int,
    text: str | None = Form(default=None),
    audio: UploadFile | None = File(default=None),
    principal: Principal = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> SubmissionResult:
    """Submit an answer and receive its evaluation.

    Multipart so text and audio can arrive together. Evaluation runs
    synchronously inside the request (decision D-4) and in the same transaction
    as the response and its audit entry.

    One response per question per candidate. No resubmission path — quietly
    overwriting something already scored and audited would be worse than a 409.
    """
    interview = repo.get_interview_for_candidate(db, interview_id, principal.id)
    if interview is None:
        raise not_found("Interview")

    question = repo.get_question_in_interview(db, question_id, interview_id)
    if question is None:
        raise not_found("Question")

    if repo.get_existing_response(db, question_id, principal.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A response for this question has already been submitted",
        )

    text = (text or "").strip() or None
    audio_path: str | None = None

    if audio is not None and audio.filename:
        try:
            audio_path = store_audio(
                filename=audio.filename, content=await audio.read()
            )
        except UploadRejected as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from None

    if text is None and audio_path is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide text, an audio file, or both",
        )

    response = Response(
        question_id=question_id,
        candidate_id=principal.id,
        text=text,
        audio_path=audio_path,
    )
    db.add(response)
    db.flush()

    evaluation = EvaluationService(db).evaluate_response(
        response,
        # The tenant is the interview's owner, never the caller: a candidate
        # must not be able to write into their own audit chain.
        tenant_admin_id=interview.owner_admin_id,
        actor_id=principal.id,
    )
    db.refresh(response)
    return SubmissionResult(response=response, evaluation=evaluation)


@router.get(
    "/me/responses/{response_id}/evaluation", response_model=CandidateEvaluationOut
)
def my_evaluation(
    response_id: int,
    principal: Principal = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateEvaluationOut:
    """A candidate's own feedback, verdict, and LLM explanation."""
    response = repo.get_response_for_candidate(db, response_id, principal.id)
    if response is None:
        raise not_found("Response")

    evaluation = response.current_evaluation
    if evaluation is None:
        raise not_found("Evaluation")

    return CandidateEvaluationOut(
        response_id=response.id,
        verdict=evaluation.verdict,
        final_verdict=evaluation.final_verdict,
        confidence=evaluation.confidence,
        explanation=evaluation.explanation,
        evaluated_at=evaluation.evaluated_at,
        awaiting_human_review=(
            evaluation.requires_human_review and evaluation.review is None
        ),
    )
