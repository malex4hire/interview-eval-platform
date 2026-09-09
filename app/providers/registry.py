"""Provider selection.

Swapping providers is a configuration change (`LLM_PROVIDER=...`), never a code
change in the pipeline. A real provider registers here and must satisfy the
same `EvaluationProvider` protocol; nothing else in the system moves.
"""

from __future__ import annotations

from typing import Callable

from app.core.config import get_settings
from app.providers.base import EvaluationProvider, Transcriber
from app.providers.mock import MockEvaluationProvider, MockTranscriber

_EVALUATION_PROVIDERS: dict[str, Callable[[str], EvaluationProvider]] = {
    "mock": lambda model: MockEvaluationProvider(model=model),
    # Registration points for real providers. Each would wrap its SDK call and
    # map the response onto EvaluationResult; the rest of the system is
    # unaffected. Left unregistered because the brief scopes out real LLM calls.
    #
    # "openai": lambda model: OpenAIEvaluationProvider(model=model),
    # "anthropic": lambda model: AnthropicEvaluationProvider(model=model),
}

_TRANSCRIBERS: dict[str, Callable[[], Transcriber]] = {
    "mock": MockTranscriber,
}


def get_evaluation_provider(name: str | None = None) -> EvaluationProvider:
    settings = get_settings()
    key = (name or settings.llm_provider).lower()
    try:
        factory = _EVALUATION_PROVIDERS[key]
    except KeyError:
        available = ", ".join(sorted(_EVALUATION_PROVIDERS))
        raise ValueError(
            f"unknown LLM provider {key!r}; registered providers: {available}"
        ) from None
    return factory(settings.llm_model_name)


def get_transcriber(name: str | None = None) -> Transcriber:
    key = (name or "mock").lower()
    try:
        return _TRANSCRIBERS[key]()
    except KeyError:
        available = ", ".join(sorted(_TRANSCRIBERS))
        raise ValueError(
            f"unknown transcriber {key!r}; registered: {available}"
        ) from None


def register_evaluation_provider(
    name: str, factory: Callable[[str], EvaluationProvider]
) -> None:
    """Extension hook — also how tests inject a controllable provider."""
    _EVALUATION_PROVIDERS[name.lower()] = factory
