"""Record an escalation and render it as a committed SVG (RST-B2).

    python -m scripts.escalation_artifact [--output-dir docs]

Two stages, one entry point:

  record   drive the real HTTP interface against a throwaway database until an
           evaluation is routed to a human, a reviewer decides it, and the
           audit chain is verified. Facts land in escalation-run.json.
  render   lay those facts out as a terminal transcript in escalation.svg.

The split matters. The JSON is what happened; the SVG is how it is drawn.
Re-running both is how the README's headline image is kept honest — see
tests/test_escalation_artifact.py, which regenerates and compares.

Nothing here reaches the network, and nothing renders at view time: the SVG is
self-contained, with no external font, stylesheet or image reference.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs"

# Settings are cached at first import, so they are fixed here before any app
# module loads. A throwaway database keeps the recording reproducible and keeps
# it away from whatever the demo instance is holding.
_RECORDING_DIR = Path(tempfile.mkdtemp(prefix="escalation-recording-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_RECORDING_DIR / 'recording.db'}"
os.environ["AUDIO_STORAGE_DIR"] = str(_RECORDING_DIR / "audio")
os.environ["JWT_SECRET"] = "recording-only-secret"
os.environ["ENVIRONMENT"] = "recording"
# The work factor protects a password database; this one is discarded.
os.environ["PBKDF2_ITERATIONS"] = "1000"
os.environ.setdefault("CONFIDENCE_THRESHOLD", "70")
os.environ.setdefault("LLM_PROVIDER", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, apply_append_only_guards, engine  # noqa: E402
from app.main import app  # noqa: E402
from app import models as _models  # noqa: E402,F401


PASSWORD = "recording-fixture-password"

# A four-concept rubric answered with exactly two. Coverage lands at 0.50,
# which is where the mock provider is least certain — the case the confidence
# gate exists for. Picked to sit in that band, never by moving the threshold.
QUESTION = "Explain database indexing and one cost of adding an index."
RUBRIC = ["b-tree", "lookup", "write overhead", "storage"]
ANSWER = (
    "An index is usually a b-tree, which makes a lookup much faster than a "
    "full scan. There is a downside to adding one but I do not remember the "
    "details."
)
REVIEW_VERDICT = "correct"
REVIEW_NOTES = (
    "Names the structure and the read benefit. The cost half is the weaker "
    "part of the rubric for this question; passing it."
)


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def record() -> dict[str, Any]:
    """Drive the API until an escalation has been raised, decided and audited."""
    engine.dispose()
    database_path = get_settings().database_url.replace("sqlite:///", "")
    Path(database_path).unlink(missing_ok=True)
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        apply_append_only_guards(connection)

    client = TestClient(app)
    calls: list[dict[str, Any]] = []

    def call(method: str, path: str, **kwargs) -> Any:
        response = client.request(method, path, **kwargs)
        calls.append({"method": method, "path": path, "status": response.status_code})
        assert response.status_code < 400, (method, path, response.status_code, response.text)
        return response.json()

    admin = call(
        "POST",
        "/auth/register",
        json={
            "email": "reviewer@example.com",
            "password": PASSWORD,
            "role": "org_admin",
            "full_name": "Dana Reyes",
        },
    )
    admin_auth = {"Authorization": f"Bearer {admin['access_token']}"}

    candidate = call(
        "POST",
        "/auth/register",
        json={
            "email": "candidate@example.com",
            "password": PASSWORD,
            "role": "candidate",
            "full_name": "Jordan Blake",
            "tenant_admin_id": admin["user_id"],
        },
    )
    candidate_auth = {"Authorization": f"Bearer {candidate['access_token']}"}

    interview = call(
        "POST",
        "/interviews",
        headers=admin_auth,
        json={
            "title": "Senior Backend Engineer - Systems Round",
            "description": "Distributed systems fundamentals.",
            "questions": [{"text": QUESTION, "expected_concepts": RUBRIC}],
        },
    )
    call(
        "POST",
        f"/interviews/{interview['id']}/assignments",
        headers=admin_auth,
        json={"candidate_id": candidate["user_id"]},
    )

    question = interview["questions"][0]
    submission = call(
        "POST",
        f"/me/interviews/{interview['id']}/questions/{question['id']}/responses",
        headers=candidate_auth,
        data={"text": ANSWER},
    )
    evaluation = submission["evaluation"]

    pending = call("GET", "/reviews/pending", headers=admin_auth)

    human_review = call(
        "POST",
        f"/evaluations/{evaluation['id']}/review",
        headers=admin_auth,
        json={"verdict": REVIEW_VERDICT, "notes": REVIEW_NOTES},
    )

    # Re-read the evaluation rather than trusting the review response: the
    # point being illustrated is that the model's verdict survives the human
    # decision, and only the stored record can show that.
    after = call(
        "GET", f"/interviews/{interview['id']}/responses", headers=admin_auth
    )[0]["current_evaluation"]

    verification = call("GET", "/audit-log/verify", headers=admin_auth)
    audit = call("GET", "/audit-log", headers=admin_auth)

    settings = get_settings()
    return {
        "generated_by": "python -m scripts.escalation_artifact",
        "provider": {"name": settings.llm_provider, "model": settings.llm_model_name},
        "confidence_threshold": settings.confidence_threshold,
        "calls": calls,
        "question": QUESTION,
        "rubric": RUBRIC,
        "answer": ANSWER,
        "evaluation": {
            "id": evaluation["id"],
            "version": evaluation["version"],
            "coverage": evaluation["coverage"],
            "confidence": evaluation["confidence"],
            "confidence_threshold": evaluation["confidence_threshold"],
            "llm_verdict": evaluation["llm_verdict"],
            "verdict": evaluation["verdict"],
            "final_verdict": evaluation["final_verdict"],
            "matched_concepts": evaluation["matched_concepts"],
            "missing_concepts": evaluation["missing_concepts"],
        },
        "pending_count": len(pending),
        "review": {
            "verdict": human_review["verdict"],
            "notes": human_review["notes"],
            "llm_verdict_after_review": after["llm_verdict"],
            "routed_verdict_after_review": after["verdict"],
            "final_verdict_after_review": after["final_verdict"],
        },
        "audit": {
            "entry_count": audit["entry_count"],
            "event_types": [entry["event_type"] for entry in audit["entries"]],
            "verification": verification,
        },
    }


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

# GitHub's dark palette, with a background painted by the artifact itself so it
# reads the same under either README theme.
COLOURS = {
    "bg": "#0d1117",
    "chrome": "#161b22",
    "border": "#30363d",
    "text": "#c9d1d9",
    "muted": "#8b949e",
    "green": "#3fb950",
    "red": "#f85149",
    "amber": "#d29922",
    "blue": "#58a6ff",
    "violet": "#bc8cff",
}

FONT_SIZE = 13
CHAR_WIDTH = 7.8  # advance width of the monospace stack at 13px
LINE_HEIGHT = 20
PAD_X = 18
CHROME_HEIGHT = 34
COLUMNS = 92


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _wrap(text: str, width: int) -> list[str]:
    """Greedy wrap. Deterministic, so the rendered height is too."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class Transcript:
    """Accumulates styled lines.

    A segment is (text, colour) or (text, colour, bold). Bold is reserved for
    the two lines that carry the escalation itself — the gate comparison and
    the routed verdict — so the eye lands there first.
    """

    def __init__(self) -> None:
        self.lines: list[list[tuple[str, ...]]] = []

    def blank(self) -> None:
        self.lines.append([])

    def add(self, *segments: tuple[str, ...]) -> None:
        self.lines.append(list(segments))

    def rule(self, width: int = COLUMNS) -> None:
        self.add(("-" * width, "border"))


LABEL_WIDTH = 14
VALUE_WIDTH = 24


def build_transcript(run: dict[str, Any]) -> Transcript:
    """Lay the recorded facts out as the terminal session that produced them.

    Every value drawn comes from `run`; nothing is written by hand here. The
    header names the command that made the recording rather than the demo
    command, because that is the one whose output this is.
    """
    evaluation = run["evaluation"]
    review = run["review"]
    audit = run["audit"]
    threshold = evaluation["confidence_threshold"]

    t = Transcript()

    def field(
        label: str, value: str, colour: str, comment: str = "", bold: bool = False
    ) -> None:
        segments: list[tuple[str, ...]] = [
            ("  " + label.ljust(LABEL_WIDTH - 2), "muted"),
            (value.ljust(VALUE_WIDTH) if comment else value, colour, bold),
        ]
        if comment:
            segments.append((comment, "muted"))
        t.add(*segments)

    t.add(
        ("$ ", "green"),
        (run["generated_by"], "text"),
    )
    t.add(
        ("  ", "muted"),
        (
            f"provider {run['provider']['name']}/{run['provider']['model']}"
            f"  ·  confidence threshold {threshold}",
            "muted",
        ),
    )
    t.blank()

    t.add(
        ("POST", "blue"),
        (" /me/interviews/1/questions/1/responses", "text"),
        ("   201", "green"),
    )
    field("rubric", " · ".join(run["rubric"]), "text")
    for index, line in enumerate(_wrap(run["answer"], COLUMNS - LABEL_WIDTH)):
        t.add(
            ("  " + ("answer".ljust(LABEL_WIDTH - 2) if index == 0 else " " * (LABEL_WIDTH - 2)), "muted"),
            (line, "text"),
        )
    t.blank()
    field("matched", ", ".join(evaluation["matched_concepts"]), "green")
    field("missing", ", ".join(evaluation["missing_concepts"]), "red")
    field(
        "coverage",
        f"{evaluation['coverage']:.2f}",
        "text",
        f"{len(evaluation['matched_concepts'])} of "
        f"{len(evaluation['matched_concepts']) + len(evaluation['missing_concepts'])}"
        " rubric concepts",
    )
    t.blank()
    field(
        "llm_verdict",
        evaluation["llm_verdict"],
        "amber",
        "what the model said — kept on the record",
    )
    field(
        "confidence",
        f"{evaluation['confidence']}  <  threshold {threshold}",
        "amber",
        "below the gate",
        bold=True,
    )
    t.rule()
    field(
        "verdict",
        evaluation["verdict"],
        "violet",
        "escalated — the model does not decide this one",
        bold=True,
    )
    t.blank()

    t.add(("GET", "blue"), (" /reviews/pending", "text"), ("   200", "green"))
    t.add(
        ("  ", "muted"),
        (f"{run['pending_count']} evaluation waiting on a human", "text"),
    )
    t.blank()

    t.add(
        ("POST", "blue"),
        (f" /evaluations/{evaluation['id']}/review", "text"),
        ("   201", "green"),
    )
    field("human", review["verdict"], "text", "a reviewer overrules the model")
    field(
        "llm_verdict",
        review["llm_verdict_after_review"],
        "amber",
        "unchanged — nothing is overwritten",
    )
    field(
        "final",
        review["final_verdict_after_review"],
        "green",
        "composed from all three positions",
    )
    t.blank()

    t.add(("GET", "blue"), (" /audit-log/verify", "text"), ("   200", "green"))
    t.add(
        ("  ", "muted"),
        (json.dumps(audit["verification"], separators=(", ", ": ")), "green"),
    )
    t.add(
        ("  ", "muted"),
        (
            f"{audit['entry_count']} hash-chained entries: "
            + ", ".join(audit["event_types"]),
            "muted",
        ),
    )
    return t


def render_svg(run: dict[str, Any]) -> str:
    transcript = build_transcript(run)
    width = int(PAD_X * 2 + COLUMNS * CHAR_WIDTH)
    height = int(CHROME_HEIGHT + LINE_HEIGHT * len(transcript.lines) + PAD_X)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Terminal transcript: an interview evaluation with confidence '
        f'{run["evaluation"]["confidence"]} falls below the threshold of '
        f'{run["evaluation"]["confidence_threshold"]} and is routed to human review.">',
        "<title>An evaluation routed to human review</title>",
        f'<rect width="{width}" height="{height}" rx="8" fill="{COLOURS["bg"]}" '
        f'stroke="{COLOURS["border"]}"/>',
        f'<path d="M0 8a8 8 0 0 1 8-8h{width - 16}a8 8 0 0 1 8 8v{CHROME_HEIGHT - 8}H0z" '
        f'fill="{COLOURS["chrome"]}"/>',
        f'<line x1="0" y1="{CHROME_HEIGHT}" x2="{width}" y2="{CHROME_HEIGHT}" '
        f'stroke="{COLOURS["border"]}"/>',
    ]
    for index, colour in enumerate(("#f85149", "#d29922", "#3fb950")):
        parts.append(f'<circle cx="{18 + index * 16}" cy="17" r="5" fill="{colour}"/>')
    parts.append(
        f'<text x="{width / 2}" y="22" text-anchor="middle" font-size="12" '
        f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
        f'fill="{COLOURS["muted"]}">confidence router — escalation to human review</text>'
    )

    parts.append(
        f'<g font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
        f'font-size="{FONT_SIZE}">'
    )
    for index, segments in enumerate(transcript.lines):
        y = CHROME_HEIGHT + 20 + index * LINE_HEIGHT
        if not segments:
            continue
        spans = "".join(
            '<tspan fill="{fill}"{weight}>{body}</tspan>'.format(
                fill=COLOURS[segment[1]],
                weight=' font-weight="700"' if len(segment) > 2 and segment[2] else "",
                body=_escape(segment[0]),
            )
            for segment in segments
        )
        # xml:space goes on each <text>: browsers do not reliably inherit it
        # from a parent <g>, and without it every column of padding collapses.
        parts.append(
            f'<text x="{PAD_X}" y="{y}" xml:space="preserve">{spans}</text>'
        )
    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="where to write escalation-run.json and escalation.svg",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    run = record()

    if run["evaluation"]["verdict"] != "requires_human_review":
        print(
            "Recorded run did not escalate: "
            f"verdict={run['evaluation']['verdict']!r} "
            f"confidence={run['evaluation']['confidence']} "
            f"threshold={run['evaluation']['confidence_threshold']}.\n"
            "The artifact would misrepresent the system; refusing to write it.",
            file=sys.stderr,
        )
        return 1

    recording_path = args.output_dir / "escalation-run.json"
    recording_path.write_text(json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    svg_path = args.output_dir / "escalation.svg"
    svg_path.write_text(render_svg(run), encoding="utf-8")

    # The throwaway database has served its purpose; leaving one behind on
    # every regeneration is litter.
    shutil.rmtree(_RECORDING_DIR, ignore_errors=True)

    print(f"recorded {recording_path.name} and rendered {svg_path.name}")
    print(
        f"  confidence {run['evaluation']['confidence']} < threshold "
        f"{run['evaluation']['confidence_threshold']} -> {run['evaluation']['verdict']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
