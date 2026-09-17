"""Gates for claims the README makes that nothing previously checked (RST-B3).

Three claims were prose only before this module existed:

  * the routing table in "Confidence threshold" — worse than unchecked, it was
    wrong. Two of its three rows misreported the seed run.
  * "switching providers is configuration, not a code change".
  * "the mock is deterministic".

Each is now measured against the running code rather than asserted in prose.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.domain.verdicts import LLMVerdict, Verdict, route_verdict
from app.models import Evaluation
from app.providers.base import EvaluationRequest, EvaluationResult
from app.providers.mock import MockEvaluationProvider
from app.providers.registry import (
    _EVALUATION_PROVIDERS,
    get_evaluation_provider,
    register_evaluation_provider,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"

ROUTING_BEGIN = "<!-- routing-table:begin -->"
ROUTING_END = "<!-- routing-table:end -->"


# ---------------------------------------------------------------------------
# the documented routing table
# ---------------------------------------------------------------------------


def documented_routing_rows() -> list[tuple[str, int, str, str]]:
    """Parse the README's routing table into comparable tuples.

    Returns (rubric_matched, confidence, llm_verdict, routed_verdict). The
    first column — the prose description of each seeded answer — is
    deliberately not compared: it is a label, not a measurement.
    """
    markdown = README.read_text(encoding="utf-8")
    start = markdown.index(ROUTING_BEGIN) + len(ROUTING_BEGIN)
    end = markdown.index(ROUTING_END)

    rows: list[tuple[str, int, str, str]] = []
    for line in markdown[start:end].splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 5:
            continue
        _label, matched, confidence, llm_verdict, routed = cells
        if not re.fullmatch(r"\d+", confidence):
            continue  # header and alignment rows
        rows.append((matched, int(confidence), llm_verdict, routed))
    assert rows, "the README routing table parsed to nothing"
    return rows


def seeded_routing_rows(db) -> list[tuple[str, int, str, str]]:
    """The same tuples, measured by running the seed script for real."""
    import scripts.seed as seed

    argv = sys.argv
    sys.argv = ["seed"]
    try:
        assert seed.main() == 0
    finally:
        sys.argv = argv

    rows: list[tuple[str, int, str, str]] = []
    for evaluation in db.execute(
        select(Evaluation).where(Evaluation.is_current.is_(True)).order_by(Evaluation.id)
    ).scalars():
        matched = len(evaluation.matched_concepts)
        total = matched + len(evaluation.missing_concepts)
        rows.append(
            (
                f"{matched} / {total}",
                evaluation.confidence,
                evaluation.llm_verdict,
                evaluation.verdict,
            )
        )
    assert rows, "the seed produced no evaluations"
    return rows


def test_the_readme_routing_table_matches_the_seeded_run(db):
    """The documented numbers are the numbers the code produces.

    This is the gate that caught the table being wrong: it claimed the strong
    answer scored 1.00/100 when the seeded answer actually covers 4 of 5
    concepts and scores 84, and that the decisively wrong answer scored 98
    when it scores 100.
    """
    assert sorted(seeded_routing_rows(db)) == sorted(documented_routing_rows())


def test_the_documented_table_covers_every_seeded_evaluation(db):
    """A table that documented only the flattering rows would still pass the
    comparison above if it were compared as a subset. It is not."""
    assert len(documented_routing_rows()) == len(seeded_routing_rows(db))


def test_the_seed_exercises_every_routing_disposition(db):
    """The README claims the seed hits every branch of the router."""
    routed = {row[3] for row in seeded_routing_rows(db)}
    assert routed == {
        Verdict.CORRECT.value,
        Verdict.INCORRECT.value,
        Verdict.REQUIRES_HUMAN_REVIEW.value,
    }


def test_confidence_is_certainty_not_score():
    """The README's central claim about the router, asserted directly.

    A decisively wrong answer is `incorrect` at high confidence and is NOT
    escalated; an ambiguous one is escalated whatever the model concluded. If
    confidence merely restated the score, the first case would be in the queue.
    """
    threshold = 70
    assert route_verdict(LLMVerdict.INCORRECT, 100, threshold) is Verdict.INCORRECT
    assert route_verdict(LLMVerdict.CORRECT, 54, threshold) is Verdict.REQUIRES_HUMAN_REVIEW
    assert route_verdict(LLMVerdict.INCORRECT, 54, threshold) is Verdict.REQUIRES_HUMAN_REVIEW


# ---------------------------------------------------------------------------
# provider swapping
# ---------------------------------------------------------------------------


class _StubProvider:
    """A second provider, registered the way a real one would be."""

    name = "stub"

    def __init__(self, model: str) -> None:
        self.model = model

    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        return EvaluationResult(
            verdict=LLMVerdict.CORRECT,
            confidence=88,
            explanation="stub",
            matched_concepts=list(request.expected_concepts),
            missing_concepts=[],
            coverage=1.0,
            prompt="stub",
        )


@pytest.fixture
def clean_registry():
    """Restore the registry: it is module-level state shared by the suite."""
    original = dict(_EVALUATION_PROVIDERS)
    yield
    _EVALUATION_PROVIDERS.clear()
    _EVALUATION_PROVIDERS.update(original)


def test_the_provider_is_selected_by_name_not_by_editing_the_pipeline(clean_registry):
    """The README: "switching providers is LLM_PROVIDER=..., not a code change"."""
    assert isinstance(get_evaluation_provider("mock"), MockEvaluationProvider)

    register_evaluation_provider("stub", lambda model: _StubProvider(model))
    selected = get_evaluation_provider("stub")

    assert isinstance(selected, _StubProvider)
    assert selected.name == "stub"
    # Nothing in the pipeline was edited to reach it: selection is a lookup.
    assert get_evaluation_provider("mock").name == "mock"


def test_an_unknown_provider_fails_loudly(clean_registry):
    """A silent fallback to the mock would make a misconfigured deployment
    look like a working one."""
    with pytest.raises(ValueError) as raised:
        get_evaluation_provider("does-not-exist")
    assert "registered providers" in str(raised.value)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_the_mock_provider_is_deterministic():
    """Same input, same evaluation — the README's stated requirement.

    Without it the audit replay is not reproducible and the committed
    escalation artifact could not be regenerated and compared.
    """
    request = EvaluationRequest(
        question_text="Explain database indexing and one cost of adding an index.",
        expected_concepts=["b-tree", "lookup", "write overhead", "storage"],
        answer_text="An index is usually a b-tree that makes a lookup faster.",
    )
    provider = MockEvaluationProvider()

    first = provider.evaluate(request)
    second = MockEvaluationProvider().evaluate(request)

    # latency_ms is wall-clock and is excluded by design, not by oversight.
    for field in (
        "verdict",
        "confidence",
        "explanation",
        "matched_concepts",
        "missing_concepts",
        "coverage",
        "prompt",
        "raw_output",
        "prompt_tokens",
        "completion_tokens",
    ):
        assert getattr(first, field) == getattr(second, field), field
