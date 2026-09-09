"""The evaluation pipeline. Plain Python, no HTTP, so a queue worker can call
it later without a rewrite.

One transaction:
    transcribe (if audio) -> provider -> route verdict -> supersede prior
    version -> persist evaluation -> llm_call ledger -> audit entry -> commit

The audit entry commits with the evaluation. If the commit fails neither
exists, so there's no window where the log and the record disagree.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.verdicts import LLMVerdict, Verdict, route_verdict
from app.models import AuditEvent, Evaluation, LLMCall, Question, Response
from app.providers.base import (
    EvaluationRequest,
    EvaluationResult,
    ProviderError,
    EvaluationProvider,
    Transcriber,
)
from app.providers.registry import get_evaluation_provider, get_transcriber
from app.services.audit_service import AuditService


class EvaluationService:
    def __init__(
        self,
        db: Session,
        provider: EvaluationProvider | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.db = db
        self.settings = get_settings()
        # Injected rather than constructed, so tests can drive a controllable
        # provider without touching configuration or the network.
        self.provider = provider or get_evaluation_provider()
        self.transcriber = transcriber or get_transcriber()
        self.audit = AuditService(db)

    # -- public -------------------------------------------------------------

    def evaluate_response(
        self,
        response: Response,
        *,
        tenant_admin_id: int,
        actor_id: int,
        reason: str | None = None,
    ) -> Evaluation:
        """Score a response, append a new evaluation version.

        v1 on first pass, N+1 on re-evaluation (D-5). Older versions are
        superseded, not deleted.
        """
        question = self.db.get(Question, response.question_id)
        if question is None:  # pragma: no cover - FK makes this unreachable
            raise ValueError(f"question {response.question_id} not found")

        answer_text = self._resolve_answer_text(response)

        request = EvaluationRequest(
            question_text=question.text,
            expected_concepts=list(question.expected_concepts or []),
            answer_text=answer_text,
        )

        try:
            result = self.provider.evaluate(request)
        except ProviderError as exc:
            # Log the failed call, then re-raise. No evaluation row — a
            # missing verdict is better than an invented one.
            self._record_failed_call(
                response=response,
                tenant_admin_id=tenant_admin_id,
                prompt=getattr(exc, "prompt", ""),
                error=str(exc),
            )
            self.db.commit()
            raise

        verdict = route_verdict(
            result.verdict, result.confidence, self.settings.confidence_threshold
        )

        previous = self._supersede_current(response.id)
        version = 1 if previous is None else previous.version + 1

        evaluation = Evaluation(
            response_id=response.id,
            version=version,
            is_current=True,
            provider=self.provider.name,
            model=self.provider.model,
            raw_llm_output=result.raw_output,
            explanation=result.explanation,
            matched_concepts=result.matched_concepts,
            missing_concepts=result.missing_concepts,
            coverage=result.coverage,
            confidence=result.confidence,
            confidence_threshold=self.settings.confidence_threshold,
            llm_verdict=result.verdict.value,
            verdict=verdict.value,
            reevaluation_reason=reason if version > 1 else None,
        )
        self.db.add(evaluation)
        self.db.flush()

        self._record_call(
            evaluation=evaluation,
            response=response,
            tenant_admin_id=tenant_admin_id,
            result=result,
        )

        self.audit.record(
            tenant_admin_id=tenant_admin_id,
            event_type=(
                AuditEvent.EVALUATION_CREATED.value
                if version == 1
                else AuditEvent.EVALUATION_REEVALUATED.value
            ),
            subject_type="evaluation",
            subject_id=evaluation.id,
            actor_id=actor_id,
            interview_id=question.interview_id,
            payload={
                "response_id": response.id,
                "question_id": question.id,
                "candidate_id": response.candidate_id,
                "version": version,
                "provider": self.provider.name,
                "model": self.provider.model,
                "prompt": result.prompt,
                "raw_llm_output": result.raw_output,
                "matched_concepts": result.matched_concepts,
                "missing_concepts": result.missing_concepts,
                "coverage": round(result.coverage, 4),
                "confidence": result.confidence,
                "confidence_threshold": self.settings.confidence_threshold,
                "llm_verdict": result.verdict.value,
                "verdict": verdict.value,
                "reevaluation_reason": reason if version > 1 else None,
            },
        )

        self.db.commit()
        self.db.refresh(evaluation)
        return evaluation

    # -- internals ----------------------------------------------------------

    def _resolve_answer_text(self, response: Response) -> str:
        """What the provider actually sees.

        Audio is transcribed once and stored, so the record shows what was
        evaluated. If both text and audio came in, the typed text wins — it's
        the candidate's words without an STT step in between.
        """
        if response.text:
            if response.transcript is None:
                response.transcript = response.text
                response.transcription_source = "submitted_text"
            return response.text

        if response.audio_path:
            if response.transcript is None:
                transcription = self.transcriber.transcribe(response.audio_path)
                response.transcript = transcription.text
                response.transcription_source = transcription.source
            return response.transcript or ""

        return ""

    def _supersede_current(self, response_id: int) -> Evaluation | None:
        stmt = (
            select(Evaluation)
            .where(Evaluation.response_id == response_id)
            .order_by(Evaluation.version.desc())
            .limit(1)
        )
        latest = self.db.execute(stmt).scalar_one_or_none()
        if latest is not None and latest.is_current:
            latest.is_current = False
            latest.superseded_at = datetime.now(timezone.utc)
            self.db.flush()
        return latest

    def _record_call(
        self,
        *,
        evaluation: Evaluation,
        response: Response,
        tenant_admin_id: int,
        result: EvaluationResult,
    ) -> LLMCall:
        cost = (
            result.prompt_tokens / 1000 * self.settings.cost_per_1k_input_tokens_usd
            + result.completion_tokens
            / 1000
            * self.settings.cost_per_1k_output_tokens_usd
        )
        call = LLMCall(
            evaluation_id=evaluation.id,
            candidate_id=response.candidate_id,
            tenant_admin_id=tenant_admin_id,
            provider=self.provider.name,
            model=self.provider.model,
            prompt=result.prompt,
            raw_response=result.raw_output,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_estimate_usd=round(cost, 6),
            latency_ms=result.latency_ms,
            succeeded=True,
        )
        self.db.add(call)
        self.db.flush()
        return call

    def _record_failed_call(
        self, *, response: Response, tenant_admin_id: int, prompt: str, error: str
    ) -> None:
        self.db.add(
            LLMCall(
                evaluation_id=None,
                candidate_id=response.candidate_id,
                tenant_admin_id=tenant_admin_id,
                provider=self.provider.name,
                model=self.provider.model,
                prompt=prompt,
                raw_response={},
                succeeded=False,
                error=error,
            )
        )
        self.db.flush()


__all__ = ["EvaluationService", "LLMVerdict", "Verdict"]
