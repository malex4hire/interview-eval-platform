"""RST-B4 — the commit history names what it satisfies.

A branch squashed to "address review feedback" tells a reviewer nothing about
which requirement each change served. This asserts the opposite: the range
contains at least one commit per RST identifier in the ICG, unsquashed.

Range resolution, in order:

  1. $RST_COMMIT_RANGE, for a caller who knows better than the heuristics.
  2. `main..HEAD`, then `origin/main..HEAD` — the feature-branch case.
  3. every commit reachable from HEAD — what remains once the branch is merged,
     and what a CI checkout of a pull-request merge ref gives.

**The fallback fails closed.** A gate has two outcomes, and "I could not work
out what to measure" is not one of them. Resolution raises `RangeUnresolvable`
rather than quietly measuring something else when:

  * this is not a git repository at all;
  * the repository is SHALLOW and no branch range could be established, so the
    full-history fallback would be reading a truncated history;
  * the resolved range contains no commits.

The shallow case is the one worth spelling out, because it is the only way the
old code could mislead. A depth-1 checkout of a merged `main` still fails — but
it used to fail reporting "no commit mentions RST-B1", which reads as *the work
was never done* when the truth is *the history is not here to look at*. Those
are different facts and the gate now says which one it found.

Post-merge on a full clone the fallback resolves to the whole history, and that
is not a vacuous pass: the identifiers still have to appear in real commit
subjects, and test_the_work_is_not_squashed_into_one_commit still requires one
subject per identifier. CI checks out with fetch-depth: 0 so this path has a
real history to read.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support.git_range import (
    RangeResolution,
    RangeUnresolvable,
    git,
    is_shallow,
    log,
    resolve_range,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# The identifiers this ICG defines. Explicit rather than scraped from a
# document: the list IS the requirement, and a scraper that found none would
# make this test pass by discovering nothing.
RST_IDENTIFIERS = ("RST-B1", "RST-B2", "RST-B3", "RST-B4", "RST-B5")


@pytest.fixture(scope="module")
def resolution() -> RangeResolution:
    try:
        return resolve_range()
    except RangeUnresolvable as exc:
        pytest.fail(f"RST-B4 cannot establish a commit range to check: {exc}")


@pytest.fixture(scope="module")
def message_lines(resolution) -> list[str]:
    """Subjects and bodies, so an identifier may be named in either."""
    return log("%s%n%b", resolution.revision_range)


@pytest.fixture(scope="module")
def subjects(resolution) -> list[str]:
    return log("%s", resolution.revision_range)


@pytest.mark.parametrize("identifier", RST_IDENTIFIERS)
def test_a_commit_names_each_rst_identifier(identifier: str, message_lines, resolution):
    """SPEC: one commit per RST minimum, each naming the identifier."""
    matching = [line for line in message_lines if identifier in line]
    assert matching, (
        f"no commit mentions {identifier} in "
        f"{resolution.revision_range or 'the full history'} "
        f"(resolved via {resolution.source}). Each RST in this ICG lands as its "
        "own commit, naming what it satisfies."
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


# ---------------------------------------------------------------------------
# the fallback itself
# ---------------------------------------------------------------------------


def _init_repo(path: Path, *subjects_to_commit: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "work", str(path), repo=path.parent)
    for setting, value in (
        ("user.name", "test"),
        ("user.email", "test@example.invalid"),
    ):
        git("config", setting, value, repo=path)
    for subject in subjects_to_commit:
        git("commit", "-q", "--allow-empty", "-m", subject, repo=path)
    return path


def test_resolution_refuses_when_the_directory_is_not_a_repository(tmp_path):
    """Fail closed, loudly, rather than measure nothing."""
    with pytest.raises(RangeUnresolvable, match="not a git repository"):
        resolve_range(tmp_path)


def test_resolution_refuses_a_shallow_history_it_cannot_trust(tmp_path, monkeypatch):
    """The fallback path's own gate.

    A depth-1 clone can see one commit. Reading that as "the full history" and
    reporting which identifiers are missing would be an accusation built on a
    truncated record, so resolution refuses instead.
    """
    monkeypatch.delenv("RST_COMMIT_RANGE", raising=False)
    deep = _init_repo(
        tmp_path / "deep",
        "RST-B1: a",
        "RST-B2: b",
        "RST-B3: c",
        "RST-B4: d",
        "later unrelated work",
    )
    shallow = tmp_path / "shallow"
    git("clone", "-q", "--depth", "1", f"file://{deep}", str(shallow), repo=tmp_path)
    assert is_shallow(shallow), "fixture precondition: the clone must be shallow"

    with pytest.raises(RangeUnresolvable, match="shallow"):
        resolve_range(shallow)


def test_resolution_refuses_an_explicit_range_that_names_nothing(tmp_path, monkeypatch):
    """An unresolvable override is refused rather than silently ignored."""
    repo = _init_repo(tmp_path / "repo", "RST-B1: a")
    monkeypatch.setenv("RST_COMMIT_RANGE", "HEAD..HEAD")
    with pytest.raises(RangeUnresolvable, match="names no commits"):
        resolve_range(repo)


def test_a_full_history_with_the_work_missing_reads_as_missing_work(
    tmp_path, monkeypatch
):
    """The other side of the distinction.

    When the history IS trustworthy and the identifiers are absent, that is a
    real finding and resolution must NOT refuse — otherwise the unresolvable
    branch would swallow the failure this gate exists to report.
    """
    monkeypatch.delenv("RST_COMMIT_RANGE", raising=False)
    repo = _init_repo(tmp_path / "plain", "unrelated work")

    resolved = resolve_range(repo)
    assert resolved.source == "full-history"
    assert not [
        line
        for line in log("%s%n%b", resolved.revision_range, repo=repo)
        if any(identifier in line for identifier in RST_IDENTIFIERS)
    ]
