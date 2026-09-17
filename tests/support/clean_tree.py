"""Materialise a pristine copy of the repository into a temp directory.

A `git clone` would only carry committed state, so a test written before its
implementation lands could never pass. Instead the tree is rebuilt from
`git ls-files --cached --others --exclude-standard`, which is exactly the set
of files a clean clone would contain once the current work is committed:
tracked files plus untracked files that are not gitignored.

What that excludes is the point of the exercise — `.venv/`, `*.db`,
`__pycache__/`, `.pytest_cache/` and `storage/audio/*` are all gitignored, so
the copy has no virtualenv, no database and no seeded artefacts. It is the
state a reviewer gets from `git clone`, nothing more.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def repo_files(repo_root: Path = REPO_ROOT) -> list[str]:
    """Paths a clean clone would contain, relative to the repository root."""
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in completed.stdout.split("\0") if path]


def materialise_clean_tree(destination: Path, repo_root: Path = REPO_ROOT) -> Path:
    """Copy the clean file set into `destination` and return it.

    File modes are preserved, so an executable entry point stays executable —
    without that, testing "run the one command" would test the wrong thing.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for relative in repo_files(repo_root):
        source = repo_root / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination
