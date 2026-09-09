"""Concept matching and confidence. A real LLM provider replaces this."""

from __future__ import annotations

import hashlib
import re
from typing import NamedTuple, Sequence

# A concept counts as matched when it appears as a whole word/phrase. Substring
# matching would score "cache" against "cacheable" and inflate results.
_WORD_SPLIT = re.compile(r"[^a-z0-9+#.]+")


class ConceptMatch(NamedTuple):
    concept: str
    matched: bool


class ScoreResult(NamedTuple):
    matched_concepts: list[str]
    missing_concepts: list[str]
    coverage: float  # 0.0 - 1.0
    confidence: int  # 0 - 100


def normalise(text: str) -> str:
    """Lowercase and collapse punctuation to single spaces for matching."""
    return " " + " ".join(t for t in _WORD_SPLIT.split(text.lower()) if t) + " "


def match_concepts(answer: str, expected_concepts: Sequence[str]) -> list[ConceptMatch]:
    """Whole-phrase, case-insensitive. Padding both sides with spaces gives a
    word-boundary check without compiling a regex per concept."""
    haystack = normalise(answer)
    results: list[ConceptMatch] = []
    for concept in expected_concepts:
        needle = normalise(concept).strip()
        results.append(ConceptMatch(concept=concept, matched=f" {needle} " in haystack))
    return results


def _deterministic_jitter(seed_material: str, spread: int = 4) -> int:
    """Keeps mock confidences from all being identical. Hash-derived, not
    random — the same response has to score the same every time."""
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    return (digest[0] % (2 * spread + 1)) - spread


def score(answer: str, expected_concepts: Sequence[str]) -> ScoreResult:
    """Score an answer and derive a confidence.

    Confidence is certainty, not correctness: highest at either extreme
    (nothing matched or everything matched are both easy calls), lowest in the
    middle where a reviewer actually helps. So `incorrect` at confidence 97 is
    expected behaviour, not a bug.
    """
    if not expected_concepts:
        # No rubric, nothing to assert. Send it to a human rather than
        # inventing a verdict.
        return ScoreResult(
            matched_concepts=[], missing_concepts=[], coverage=0.0, confidence=0
        )

    matches = match_concepts(answer, expected_concepts)
    matched = [m.concept for m in matches if m.matched]
    missing = [m.concept for m in matches if not m.matched]
    coverage = len(matched) / len(expected_concepts)

    # 0.0 at maximal ambiguity (coverage 0.5), 1.0 at either extreme.
    decisiveness = abs(coverage - 0.5) * 2
    base = 55 + 45 * decisiveness
    confidence = int(round(base)) + _deterministic_jitter(f"{answer}|{coverage}")

    return ScoreResult(
        matched_concepts=matched,
        missing_concepts=missing,
        coverage=coverage,
        confidence=max(0, min(100, confidence)),
    )


# Coverage at or above this is asserted as correct by the mock provider.
PASS_COVERAGE = 0.6
