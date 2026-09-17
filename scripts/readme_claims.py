"""Parse the README's claims register (RST-B3).

Every capability claim the README makes is registered in one table, and each
row names both the artifact that implements it and the gate that proves it.
This module is the single reader of that table: the test suite imports it to
check the bindings resolve, and CI runs it to turn the table into the exact
list of node ids it must execute.

One reader, two consumers. A second parser would drift from the first, and the
drift would silently unbind whichever claims the two disagreed about.

    python -m scripts.readme_claims            # node ids, one per line
    python -m scripts.readme_claims --json     # the parsed register
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"

# The table is delimited rather than located by heading text, so the heading
# can be reworded without silently unbinding every claim in the repository.
BEGIN = "<!-- claims:begin -->"
END = "<!-- claims:end -->"

_CODE_SPAN = re.compile(r"`([^`]+)`")
_PYTEST_NODE = re.compile(r"^tests/[\w/]+\.py::[\w\[\]\-.]+$")
_WORKFLOW_JOB = re.compile(r"^\.github/workflows/[\w.-]+\.yml::[\w-]+$")


class Claim(NamedTuple):
    identifier: str
    text: str
    artifacts: list[str]
    gates: list[str]

    @property
    def pytest_gates(self) -> list[str]:
        return [gate for gate in self.gates if _PYTEST_NODE.match(gate)]

    @property
    def workflow_gates(self) -> list[str]:
        return [gate for gate in self.gates if _WORKFLOW_JOB.match(gate)]


class ClaimsRegisterError(RuntimeError):
    """The register is missing or malformed. Never silently tolerated: an
    unreadable register would make every binding check pass vacuously."""


def register_source(readme: Path = README) -> str:
    markdown = readme.read_text(encoding="utf-8")
    try:
        start = markdown.index(BEGIN) + len(BEGIN)
        end = markdown.index(END)
    except ValueError as exc:
        raise ClaimsRegisterError(
            f"{readme.name} has no claims register; expected {BEGIN} ... {END}"
        ) from exc
    if end < start:
        raise ClaimsRegisterError("claims register markers are out of order")
    return markdown[start:end]


def parse_claims(readme: Path = README) -> list[Claim]:
    """Read the register into rows. Raises rather than returning [] on damage."""
    claims: list[Claim] = []
    for line in register_source(readme).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            # Skipping here would be a fail-open: a claim whose text contains a
            # stray pipe would drop out of the register silently, and every
            # check downstream would pass because it no longer knew about it.
            raise ClaimsRegisterError(
                f"claims row does not have 4 cells (found {len(cells)}): {line!r}. "
                "A '|' inside a cell splits the row. Write it as '&#124;', "
                "which GitHub renders as a pipe and this parser does not split "
                "on. A backslash escape does not help here - the split happens "
                "before any markdown escaping is considered."
            )
        identifier, text, implemented_in, proven_by = cells
        # Header row and the alignment row underneath it.
        if identifier.lower() in {"#", "id", ""} or set(identifier) <= set("-: "):
            continue
        claims.append(
            Claim(
                identifier=identifier,
                text=text,
                artifacts=_CODE_SPAN.findall(implemented_in),
                gates=_CODE_SPAN.findall(proven_by),
            )
        )
    if not claims:
        raise ClaimsRegisterError("claims register contains no rows")
    return claims


def pytest_node_ids(readme: Path = README) -> list[str]:
    """Every distinct pytest gate named by the register, in register order."""
    seen: dict[str, None] = {}
    for claim in parse_claims(readme):
        for gate in claim.pytest_gates:
            seen.setdefault(gate, None)
    return list(seen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true", help="emit the whole parsed register"
    )
    args = parser.parse_args()

    if args.json:
        json.dump(
            [claim._asdict() for claim in parse_claims()], sys.stdout, indent=2
        )
        sys.stdout.write("\n")
    else:
        for node_id in pytest_node_ids():
            print(node_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
