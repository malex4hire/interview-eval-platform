"""Deterministic offline provider and transcriber.

Determinism is a requirement, not a convenience: the same response must always
produce the same evaluation, or tests are flaky and an audit replay cannot be
reproduced. Nothing here uses randomness or wall-clock-dependent behaviour.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.domain import scoring
from app.domain.verdicts import LLMVerdict
from app.providers.base import (
    EvaluationRequest,
    EvaluationResult,
    TranscriptionResult,
)

PROMPT_TEMPLATE = """You are evaluating a candidate's interview answer.

QUESTION:
{question}

EXPECTED CONCEPTS (the rubric):
{concepts}

CANDIDATE ANSWER:
{answer}

Decide whether the answer is correct or incorrect against the rubric. Return a
verdict, the concepts you found, the concepts that are missing, a 0-100
confidence in your own judgement, and a short explanation."""


def build_prompt(request: EvaluationRequest) -> str:
    concepts = "\n".join(f"- {c}" for c in request.expected_concepts) or "- (none)"
    return PROMPT_TEMPLATE.format(
        question=request.question_text,
        concepts=concepts,
        answer=request.answer_text or "(no answer provided)",
    )


def estimate_tokens(text: str) -> int:
    """~4 characters per token. Adequate for a mocked cost ledger."""
    return max(1, len(text) // 4)


class MockEvaluationProvider:
    """Rubric-based scorer standing in for a real LLM.

    Verdict comes from concept coverage; confidence comes from how *decisive*
    that coverage is (see app/domain/scoring.score). A confidently-wrong result
    is therefore reachable, which is the behaviour that makes the human-review
    gate meaningful rather than a proxy for "low score".
    """

    name = "mock"

    def __init__(self, model: str = "mock-eval-v1") -> None:
        self.model = model

    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        started = time.perf_counter()
        prompt = build_prompt(request)

        result = scoring.score(request.answer_text, request.expected_concepts)
        verdict = (
            LLMVerdict.CORRECT
            if result.coverage >= scoring.PASS_COVERAGE
            else LLMVerdict.INCORRECT
        )

        explanation = self._explain(verdict, result, request)
        raw_output = {
            "model": self.model,
            "verdict": verdict.value,
            "confidence": result.confidence,
            "explanation": explanation,
            "matched_concepts": result.matched_concepts,
            "missing_concepts": result.missing_concepts,
            "coverage": round(result.coverage, 4),
            "rubric_size": len(request.expected_concepts),
        }

        latency_ms = int((time.perf_counter() - started) * 1000)
        return EvaluationResult(
            verdict=verdict,
            confidence=result.confidence,
            explanation=explanation,
            matched_concepts=result.matched_concepts,
            missing_concepts=result.missing_concepts,
            coverage=result.coverage,
            prompt=prompt,
            raw_output=raw_output,
            prompt_tokens=estimate_tokens(prompt),
            completion_tokens=estimate_tokens(explanation),
            latency_ms=latency_ms,
        )

    @staticmethod
    def _explain(
        verdict: LLMVerdict, result: scoring.ScoreResult, request: EvaluationRequest
    ) -> str:
        total = len(request.expected_concepts)
        found = ", ".join(result.matched_concepts) or "none"
        missing = ", ".join(result.missing_concepts) or "none"
        return (
            f"Based on the rubric, the answer covers {len(result.matched_concepts)}"
            f" of {total} expected concepts ({result.coverage:.0%}). "
            f"Found: {found}. Missing: {missing}. "
            f"Verdict {verdict.value} with confidence {result.confidence}."
        )


class MockTranscriber:
    """Stand-in for speech-to-text.

    If the uploaded file decodes as UTF-8 text its contents are used as the
    transcript — that is how the seed fixtures carry realistic audio answers
    without shipping real audio or an STT dependency. Anything else yields a
    deterministic placeholder derived from the filename, so the pipeline still
    runs end to end on genuine binary uploads.
    """

    name = "mock-transcriber"

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        path = Path(audio_path)
        try:
            decoded = path.read_bytes().decode("utf-8").strip()
            if decoded:
                return TranscriptionResult(text=decoded, source=f"{self.name}:passthrough")
        except (UnicodeDecodeError, OSError):
            pass
        return TranscriptionResult(
            text=f"[mock transcript unavailable for {path.name}]",
            source=f"{self.name}:placeholder",
        )
