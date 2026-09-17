"""RST-B4 — the commit history names what it satisfies.

A branch squashed to "address review feedback" tells a reviewer nothing about
which requirement each change served. This asserts the opposite: the range
contains at least one commit per RST identifier in the ICG, unsquashed.

Range resolution, in order:

  1. $RST_COMMIT_RANGE, for a caller who knows better than the heuristics.
  2. `main..HEAD`, then `origin/main..HEAD` — the feature-branch case.
  3. every commit reachable from HEAD, which is what remains once the branch is
     merged, and what a CI checkout of a pull-request merge ref gives.

Falling back rather than skipping is deliberate. A skip here would let the
requirement evaporate in exactly the situation it exists to survive.

CI checks this repository out with fetch-depth: 0. Under the default depth of
1 the history is truncated to a single commit and step 3 would find nothing —
so the workflow's fetch-depth is part of this gate, not an optimisation.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The identifiers this ICG defines. Explicit rather than scraped from a
# document: the list IS the requirement, and a scraper that found none would
# make this test pass by discovering nothing.
RST_IDENTIFIERS = ("RST-B1", "RST-B2", "RST-B3", "RST-B4")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def _log(fmt: str, revision_range: str | None) -> list[str]:
    args = ["log", f"--format={fmt}", "--no-merges"]
    if revision_range:
        args.append(revision_range)
    completed = _git(*args)
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _resolve_range() -> str | None:
    """The range to inspect, or None meaning "everything reachable from HEAD"."""
    explicit = os.environ.get("RST_COMMIT_RANGE")
    if explicit:
        return explicit
    for base in ("main", "origin/main"):
        if _git("rev-parse", "--verify", "--quiet", base).returncode != 0:
            continue
        if _log("%s", f"{base}..HEAD"):
            return f"{base}..HEAD"
    return None


@pytest.fixture(scope="module")
def revision_range() -> str | None:
    if _git("rev-parse", "--git-dir").returncode != 0:
        pytest.fail("not a git repository, so the commit history cannot be checked")
    return _resolve_range()


@pytest.fixture(scope="module")
def message_lines(revision_range) -> list[str]:
    """Subjects and bodies, so an identifier may be named in either."""
    lines = _log("%s%n%b", revision_range)
    assert lines, (
        f"no commits found in range {revision_range or 'HEAD'}. If this is CI, "
        "the checkout is probably shallow — RST-B4 needs fetch-depth: 0."
    )
    return lines


@pytest.fixture(scope="module")
def subjects(revision_range) -> list[str]:
    return _log("%s", revision_range)


@pytest.mark.parametrize("identifier", RST_IDENTIFIERS)
def test_a_commit_names_each_rst_identifier(identifier: str, message_lines):
    """SPEC: one commit per RST minimum, each naming the identifier."""
    matching = [line for line in message_lines if identifier in line]
    assert matching, (
        f"no commit in the range mentions {identifier}. "
        "Each RST in this ICG lands as its own commit, naming what it satisfies."
    )


def test_the_work_is_not_squashed_into_one_commit(subjects):
    """SPEC: no squash to a single commit.

    Counted over subjects alone. The check above reads bodies too, so one
    commit listing all four identifiers in its body would otherwise satisfy it
    while defeating the requirement this test exists for.
    """
    naming = [
        subject
        for subject in subjects
        if any(identifier in subject for identifier in RST_IDENTIFIERS)
    ]
    assert len(naming) >= len(RST_IDENTIFIERS), (
        f"only {len(naming)} commit subject(s) name an RST identifier "
        f"({naming}); RST-B4 requires at least one commit per identifier."
    )


def test_identifiers_are_named_in_a_recognisable_form():
    """Guards the constant itself: a typo would silently match nothing, and the
    parametrised test would report it as missing work rather than as a typo."""
    for identifier in RST_IDENTIFIERS:
        assert re.fullmatch(r"RST-B\d", identifier), identifier
