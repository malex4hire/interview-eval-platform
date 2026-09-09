"""Provider contracts.

Nothing above here knows whether an evaluation came from a mock or a real
model; nothing below knows about HTTP, the ORM or the audit log.

The contract is narrow. A provider gets the rubric and the answer, returns a verdict, explanation, confidence and raw output.
Thresholding and human-review routing are ours (see domain/verdicts.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from app.domain.verdicts import LLMVerdict


@dataclass(frozen=True)
class EvaluationRequest:
    question_text: str
    expected_concepts: Sequence[str]
    answer_text: str


@dataclass(frozen=True)
class EvaluationResult:
    """A provider's output. `raw_output` is stored verbatim for audit."""

    verdict: LLMVerdict
    confidence: int  # 0-100
    explanation: str
    matched_concepts: list[str]
    missing_concepts: list[str]
    coverage: float
    prompt: str
    raw_output: dict[str, Any] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 100:
            raise ValueError(f"confidence must be 0-100, got {self.confidence}")


@runtime_checkable
class EvaluationProvider(Protocol):
    """Any scoring backend. Implementations must be side-effect free."""

    name: str
    model: str

    def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    source: str  # which transcriber produced it — recorded on the response


@runtime_checkable
class Transcriber(Protocol):
    """Audio -> text. Mocked here; a real STT service swaps in unchanged."""

    name: str

    def transcribe(self, audio_path: str) -> TranscriptionResult: ...


class ProviderError(RuntimeError):
    """Raised by a provider on an unrecoverable failure.

    Distinct from a low-confidence result: a failure produces no evaluation and
    is recorded in the llm_calls ledger with succeeded=False.
    """
