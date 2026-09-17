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


def test_a_later_branch_that_lands_no_rst_work_still_passes(tmp_path, monkeypatch):
    """The regression that blocked the first pull request after this merged.

    Resolution used to prefer `main..HEAD`, so a branch was required to name
    every RST identifier itself. The first follow-up — a docs-only change —
    failed with "no commit mentions RST-B2 in origin/main..HEAD", and so would
    every future branch, because no later branch re-lands the original work.
    The requirement is historical: the identifiers must be IN the history, not
    in whatever range happens to be checked out.
    """
    monkeypatch.delenv("RST_COMMIT_RANGE", raising=False)
    repo = _init_repo(
        tmp_path / "later-branch",
        "RST-B1: a", "RST-B2: b", "RST-B3: c", "RST-B4: d", "RST-B5: e",
    )
    # main exists and points at the landed work; the branch adds only docs.
    git("branch", "-f", "main", "HEAD", repo=repo)
    git("checkout", "-q", "-b", "docs/unrelated", repo=repo)
    git("commit", "-q", "--allow-empty", "-m", "docs: unrelated follow-up", repo=repo)

    resolved = resolve_range(repo)
    assert resolved.source == "full-history"

    messages = log("%s%n%b", resolved.revision_range, repo=repo)
    for identifier in RST_IDENTIFIERS:
        assert [line for line in messages if identifier in line], (
            f"{identifier} is in the history but the resolved range cannot see it"
        )


def test_rewriting_the_identifiers_out_of_history_is_still_caught(tmp_path, monkeypatch):
    """The inverse row, so full-history is not just a way of always passing.

    If the history genuinely does not carry the work, the gate must still fail.
    """
    monkeypatch.delenv("RST_COMMIT_RANGE", raising=False)
    repo = _init_repo(tmp_path / "rewritten", "chore: squashed everything")

    resolved = resolve_range(repo)
    messages = log("%s%n%b", resolved.revision_range, repo=repo)
    missing = [i for i in RST_IDENTIFIERS if not [l for l in messages if i in l]]
    assert missing == list(RST_IDENTIFIERS)


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


def test_resolution_refuses_a_shallow_clone_that_still_has_a_branch_range(
    tmp_path, monkeypatch
):
    """Review finding 4, pinned — and this was the shape that got through.

    The shallow guard used to sit AFTER both branch-range attempts, so it only
    covered the full-history fallback. `git clone --no-single-branch --depth 2`
    leaves `origin/main` resolvable, so `origin/main..HEAD` succeeded, returned
    the two commits it could see, and the caller reported the three older RST
    commits as missing work. Measured before the fix:

        resolved='origin/main..HEAD' via origin/main
        visible=['RST-B5: e', 'RST-B4: d']
        MISSING -> ['RST-B1', 'RST-B2', 'RST-B3']

    Every one of those exists in the real history. That is the false accusation
    the refusal is for, reached by the commonest shallow shape there is.
    """
    monkeypatch.delenv("RST_COMMIT_RANGE", raising=False)
    deep = tmp_path / "deep"
    deep.mkdir()
    git("init", "-q", "-b", "main", str(deep), repo=tmp_path)
    git("config", "user.name", "test", repo=deep)
    git("config", "user.email", "test@example.invalid", repo=deep)
    git("commit", "-q", "--allow-empty", "-m", "base on main", repo=deep)
    git("checkout", "-q", "-b", "work", repo=deep)
    for subject in ("RST-B1: a", "RST-B2: b", "RST-B3: c", "RST-B4: d", "RST-B5: e"):
        git("commit", "-q", "--allow-empty", "-m", subject, repo=deep)

    shallow = tmp_path / "shallow"
    git(
        "clone", "-q", "--no-single-branch", "--depth", "2", "--branch", "work",
        f"file://{deep}", str(shallow), repo=tmp_path,
    )
    assert is_shallow(shallow), "fixture precondition: the clone must be shallow"
    assert (
        git("rev-parse", "--verify", "--quiet", "origin/main", repo=shallow).returncode
        == 0
    ), "fixture precondition: origin/main must resolve, or this tests nothing"

    with pytest.raises(RangeUnresolvable, match="shallow"):
        resolve_range(shallow)


def test_an_explicit_range_still_works_on_a_shallow_clone(tmp_path, monkeypatch):
    """The inverse row.

    Refusing every shallow repository outright would be indistinguishable from
    the guard working. An explicit override is deliberate operator intent and
    must still resolve.
    """
    deep = tmp_path / "deep2"
    deep.mkdir()
    git("init", "-q", "-b", "main", str(deep), repo=tmp_path)
    git("config", "user.name", "test", repo=deep)
    git("config", "user.email", "test@example.invalid", repo=deep)
    for subject in ("base", "RST-B1: a", "RST-B2: b"):
        git("commit", "-q", "--allow-empty", "-m", subject, repo=deep)

    shallow = tmp_path / "shallow2"
    git("clone", "-q", "--depth", "2", f"file://{deep}", str(shallow), repo=tmp_path)
    assert is_shallow(shallow)

    monkeypatch.setenv("RST_COMMIT_RANGE", "HEAD~1..HEAD")
    resolved = resolve_range(shallow)
    assert resolved.source == "RST_COMMIT_RANGE"


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
