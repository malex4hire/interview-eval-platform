"""Resolving which commits a gate should look at, or refusing to guess.

Shared by RST-B4 (every identifier is named by a commit) and RST-B5 (a commit
that raises the claims cap carries a decision entry). Both ask the same
question — *which commits are this branch's work?* — and a second copy of the
answer would drift from the first.

The contract is that resolution **fails closed**. A gate has two outcomes, and
"I could not work out what to measure" is neither of them, so this raises
rather than quietly measuring something else.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]


class RangeUnresolvable(RuntimeError):
    """No trustworthy commit range could be established.

    Deliberately distinct from "the work is missing". Conflating them is how a
    truncated checkout ends up accusing a branch of not having done its job.
    """


class RangeResolution(NamedTuple):
    revision_range: str | None  # None means "everything reachable from HEAD"
    source: str


def git(*args: str, repo: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def log(fmt: str, revision_range: str | None, repo: Path = REPO_ROOT) -> list[str]:
    args = ["log", f"--format={fmt}", "--no-merges"]
    if revision_range:
        args.append(revision_range)
    completed = git(*args, repo=repo)
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def commits(revision_range: str | None, repo: Path = REPO_ROOT) -> list[str]:
    """Commit SHAs in the range, oldest first."""
    args = ["log", "--format=%H", "--no-merges", "--reverse"]
    if revision_range:
        args.append(revision_range)
    completed = git(*args, repo=repo)
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def is_shallow(repo: Path = REPO_ROOT) -> bool:
    probe = git("rev-parse", "--is-shallow-repository", repo=repo)
    if probe.returncode == 0 and probe.stdout.strip() in {"true", "false"}:
        return probe.stdout.strip() == "true"
    return (repo / ".git" / "shallow").exists()


def resolve_range(repo: Path = REPO_ROOT) -> RangeResolution:
    """Work out what to measure, or refuse. Never guesses.

    Order: $RST_COMMIT_RANGE, then `main..HEAD`, then `origin/main..HEAD`, then
    everything reachable from HEAD — which is what remains once the branch is
    merged, and what a CI checkout of a pull-request merge ref gives.

    The last step is only trustworthy when the full history is actually
    present, so a shallow repository that got that far is refused rather than
    measured.
    """
    if git("rev-parse", "--git-dir", repo=repo).returncode != 0:
        raise RangeUnresolvable(f"{repo} is not a git repository")

    explicit = os.environ.get("RST_COMMIT_RANGE")
    if explicit:
        if not log("%s", explicit, repo=repo):
            raise RangeUnresolvable(f"RST_COMMIT_RANGE={explicit!r} names no commits")
        return RangeResolution(explicit, "RST_COMMIT_RANGE")

    for base in ("main", "origin/main"):
        if git("rev-parse", "--verify", "--quiet", base, repo=repo).returncode != 0:
            continue
        if log("%s", f"{base}..HEAD", repo=repo):
            return RangeResolution(f"{base}..HEAD", base)

    if is_shallow(repo):
        raise RangeUnresolvable(
            "no branch range could be established and the repository is "
            "shallow, so the full history is not available to fall back on. "
            "Check out with fetch-depth: 0, or set RST_COMMIT_RANGE."
        )
    if not log("%s", None, repo=repo):
        raise RangeUnresolvable("the repository has no commits")
    return RangeResolution(None, "full-history")
