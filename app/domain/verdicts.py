"""Verdict types and the routing rule between model and human."""

from __future__ import annotations

from enum import Enum


class LLMVerdict(str, Enum):
    """What the model said, before the confidence gate.

    No REQUIRES_HUMAN_REVIEW here — routing is our policy, not a model output.
    Mixing them loses the answer to "what did the model actually say".
    """

    CORRECT = "correct"
    INCORRECT = "incorrect"


class Verdict(str, Enum):
    """The evaluation's routed state after the confidence gate."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class HumanVerdict(str, Enum):
    """A reviewer's call. Always correct or incorrect."""

    CORRECT = "correct"
    INCORRECT = "incorrect"


def route_verdict(
    llm_verdict: LLMVerdict, confidence: int, threshold: int
) -> Verdict:
    """Apply the confidence gate.

    Below threshold, the model's answer stays on the record but doesn't stand
    as the outcome. Strictly less-than, so the threshold reads as the minimum
    acceptable confidence.
    """
    if not 0 <= confidence <= 100:
        raise ValueError(f"confidence must be 0-100, got {confidence}")
    if confidence < threshold:
        return Verdict.REQUIRES_HUMAN_REVIEW
    return Verdict(llm_verdict.value)


def can_be_human_reviewed(verdict: Verdict) -> bool:
    """Only flagged evaluations can be overridden (D-7). Letting an admin
    overturn a high-confidence verdict makes the threshold decorative."""
    return verdict is Verdict.REQUIRES_HUMAN_REVIEW


def final_verdict(
    verdict: Verdict, human_verdict: HumanVerdict | None
) -> Verdict:
    """A human decision supersedes the routed verdict."""
    if human_verdict is not None:
        return Verdict(human_verdict.value)
    return verdict
