"""RST-B2 — the escalation is visible without running anything.

A reviewer who never clones the repository should still see the confidence
router escalate a case. That means a committed image, referenced above the
first section heading, derived from a real recorded run rather than drawn by
hand — and regenerable, so it cannot quietly drift from the code.

The regeneration check is the load-bearing one. Without it the artifact is a
picture that was true once.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
ARTIFACT = REPO_ROOT / "docs" / "escalation.svg"
RECORDING = REPO_ROOT / "docs" / "escalation-run.json"
ENTRY_POINT = "scripts.escalation_artifact"

# The volatile-field allowlist, as regexes over the rendered artifact.
# Everything outside this list must reproduce byte for byte.
_VOLATILE = (
    # timestamps: ISO-8601, with or without an offset
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<timestamp>"),
    # elapsed durations
    (re.compile(r"\b\d+(?:\.\d+)?\s?ms\b"), "<duration>"),
    # generated identifiers: hash chain digests and uuid4 filenames
    (re.compile(r"\b[0-9a-f]{8,64}\b"), "<digest>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    # port numbers
    (re.compile(r":\d{4,5}\b"), ":<port>"),
)


def normalise_volatile(text: str) -> str:
    for pattern, placeholder in _VOLATILE:
        text = pattern.sub(placeholder, text)
    return text


def first_section_heading_offset(markdown: str) -> int:
    """Character offset of the first `##`-or-deeper heading.

    The H1 is the document title, not a section. "Above the first section
    heading" therefore means: before the first heading of level 2 or below.
    """
    match = re.search(r"^#{2,6}\s", markdown, flags=re.MULTILINE)
    assert match, "README has no section headings at all"
    return match.start()


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory) -> str:
    """Run the single entry point into a temp directory; return the new SVG."""
    output_dir = tmp_path_factory.mktemp("escalation")
    completed = subprocess.run(
        [sys.executable, "-m", ENTRY_POINT, "--output-dir", str(output_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, (
        f"`python -m {ENTRY_POINT}` failed\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    regenerated_svg = output_dir / "escalation.svg"
    assert regenerated_svg.is_file(), "the entry point produced no escalation.svg"
    return regenerated_svg.read_text(encoding="utf-8")


def test_the_artifact_is_committed():
    assert ARTIFACT.is_file(), f"{ARTIFACT.relative_to(REPO_ROOT)} is not committed"
    assert ARTIFACT.stat().st_size > 0


def test_the_recording_it_derives_from_is_committed():
    """SPEC: derived from a recorded run, not drawn by hand."""
    assert RECORDING.is_file(), f"{RECORDING.relative_to(REPO_ROOT)} is not committed"


def test_the_readme_shows_it_above_the_first_section_heading():
    markdown = README.read_text(encoding="utf-8")
    reference = "docs/escalation.svg"
    assert reference in markdown, "the README never references the artifact"

    position = markdown.index(reference)
    assert position < first_section_heading_offset(markdown), (
        "the escalation artifact is referenced below the first section "
        "heading; RST-B2 requires it above the fold"
    )


# `xmlns="http://www.w3.org/2000/svg"` is a namespace NAME, not a location:
# nothing is ever fetched from it. Stripping the declarations before looking
# for URLs is the difference between a check and a false positive.
_XMLNS_DECLARATION = re.compile(r'\sxmlns(?::\w+)?="[^"]*"')


def test_the_artifact_needs_no_network_to_render():
    """SPEC: no external hosting, no third-party embed, no fetch at render."""
    svg = _XMLNS_DECLARATION.sub("", ARTIFACT.read_text(encoding="utf-8"))

    for forbidden in (
        "http://",
        "https://",
        "//",              # protocol-relative reference
        "xlink:href",      # the classic external-image escape hatch
        "<image",
        "@import",
        "url(",            # CSS fetch, including fonts
        "<foreignObject",  # would render as HTML, and not at all inside an <img>
        "<script",
    ):
        assert forbidden not in svg, (
            f"{forbidden!r} in the artifact would make rendering depend on "
            "something outside the repository"
        )


def test_the_network_check_would_notice_an_external_reference():
    """Mutation check on the check above.

    Stripping xmlns declarations is only safe if a real external reference
    still trips the assertion afterwards. Without this, a looser strip could
    silently neuter the guard and nothing would say so.
    """
    smuggled = ARTIFACT.read_text(encoding="utf-8").replace(
        "<title>", '<image href="https://example.invalid/logo.png"/><title>', 1
    )
    stripped = _XMLNS_DECLARATION.sub("", smuggled)
    assert "https://" in stripped and "<image" in stripped


def test_the_artifact_leaks_no_paths_or_hostnames():
    """Guardrail: scrub hostnames and filesystem paths from captured output."""
    svg = ARTIFACT.read_text(encoding="utf-8")
    assert "/home/" not in svg
    assert "/Users/" not in svg
    assert "/tmp/" not in svg
    assert "testserver" not in svg
    assert "127.0.0.1" not in svg
    # The demo password is fixture data, but it has no business in an image.
    assert "demo-password" not in svg


def test_the_artifact_actually_shows_an_escalation():
    """The artifact has to depict the thing it is cited for."""
    svg = ARTIFACT.read_text(encoding="utf-8")
    assert "requires_human_review" in svg
    assert "threshold" in svg


def test_a_fresh_regeneration_matches_the_committed_artifact(regenerated):
    """The committed artifact is reproducible, modulo the volatile allowlist."""
    committed = ARTIFACT.read_text(encoding="utf-8")
    assert normalise_volatile(regenerated) == normalise_volatile(committed), (
        "regenerating the escalation artifact produced a different image.\n"
        f"Re-run `python -m {ENTRY_POINT}` and commit the result."
    )


def test_the_normaliser_does_not_hide_a_real_difference():
    """Mutation check on the comparison itself.

    Normalising is only safe if it erases volatile fields and nothing else. A
    changed verdict must still survive normalisation and fail the comparison —
    otherwise the regeneration test passes over a genuinely stale artifact.
    """
    original = ARTIFACT.read_text(encoding="utf-8")
    tampered = original.replace("requires_human_review", "correct", 1)
    assert tampered != original, "fixture precondition: the string must be present"
    assert normalise_volatile(tampered) != normalise_volatile(original)
