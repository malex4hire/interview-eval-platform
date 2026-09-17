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

# RST-B5 — the register is bounded.
#
# This repository is depth evidence, not the first thing read (D-1), and README
# surface competes with the artifact above the fold for the only thirty seconds
# that matter. Twenty claims already exceeds that budget; the constraint is that
# it stops there, not that it shrinks.
#
# Raising this number requires a decision entry in docs/DECISIONS.md in the SAME
# commit — enforced by tests/test_readme_claims.py, not by convention. The cap
# is a deliberate decision each time it moves, or it is not a cap.
#
#   Raising it:   add a line to docs/DECISIONS.md naming the constant and the
#                 new value, e.g. "REGISTER_CAP raised to 21 because ...".
#
#   After the fact, if an unlogged raise already landed: add a line naming the
#                 constant, the value AND the offending commit's sha, e.g.
#                 "REGISTER_CAP raised to 21 in 8b4fab4c, accepted after the
#                 fact." The check reads docs/DECISIONS.md at HEAD rather than
#                 the offending commit's diff, so a later commit can supply it.
#                 History is never rewritten to clear this.
REGISTER_CAP = 20

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


def register_rows(readme: Path = README) -> list[tuple[int, str]]:
    """Every non-blank line between the markers, with its line number.

    The denominator for the structural check: whatever is in here must come
    back out as a header row, an alignment row, or a claim. Nothing may be
    quietly passed over.
    """
    return [
        (number, line.strip())
        for number, line in enumerate(register_source(readme).splitlines(), start=1)
        if line.strip()
    ]


def parse_claims(readme: Path = README) -> list[Claim]:
    """Read the register into rows.

    Every non-blank line is accounted for as exactly one of: the header row,
    the alignment row, or a claim. Anything else raises.

    That total-accounting property is the point, not a nicety. A parser that
    skips a line it cannot read goes on reporting green while checking one
    fewer claim than the register appears to contain — the same shape of
    defect as a README table that five green CI jobs never looked at. Five
    such skips used to live here: a row that lost its leading pipe (which is
    still valid GitHub-flavoured markdown), a row whose identifier cell was
    blank, stray prose someone left between the markers, and a repeated header
    or alignment row, which fell through and registered as a claim named '#'
    or '---'.

    The mechanism is the refusals below and nothing else. An arithmetic
    cross-check (claims + 2 == lines) used to sit at the end of this function
    and was removed: every path above either raises or accounts for its line,
    so the sum held by construction and the check could not fail. Deleting it
    turned nothing red, which is the definition of decoration.
    """
    claims: list[Claim] = []
    header_seen = False
    alignment_seen = False

    for number, line in register_rows(readme):
        if not line.startswith("|"):
            raise ClaimsRegisterError(
                f"line {number} of the claims register is not a table row: "
                f"{line!r}. Every non-blank line between the markers must be a "
                "row starting with '|'. Skipping it would drop a claim in "
                "silence, which is the failure this parser exists to avoid."
            )

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            raise ClaimsRegisterError(
                f"line {number}: claims row does not have 4 cells "
                f"(found {len(cells)}): {line!r}. A '|' inside a cell splits "
                "the row. Write it as '&#124;', which GitHub renders as a pipe "
                "and this parser does not split on. A backslash escape does "
                "not help - the split happens before any markdown escaping is "
                "considered."
            )

        looks_like_header = cells[0].casefold() in {"#", "id"}
        looks_like_alignment = all(cell and set(cell) <= set("-:") for cell in cells)

        if looks_like_header:
            if header_seen:
                raise ClaimsRegisterError(
                    f"line {number}: a second header row: {line!r}. Repeated "
                    "structure rows used to fall through and be registered as "
                    "claims named '#' or '---'."
                )
            header_seen = True
            continue

        if looks_like_alignment:
            if alignment_seen:
                raise ClaimsRegisterError(
                    f"line {number}: a second alignment row: {line!r}. Repeated "
                    "structure rows used to fall through and be registered as "
                    "claims named '#' or '---'."
                )
            alignment_seen = True
            continue

        identifier = cells[0]
        if not identifier:
            raise ClaimsRegisterError(
                f"line {number}: claim row has an empty identifier: {line!r}. "
                "An unidentified claim used to be discarded here without a word."
            )

        claims.append(
            Claim(
                identifier=identifier,
                text=cells[1],
                artifacts=_CODE_SPAN.findall(cells[2]),
                gates=_CODE_SPAN.findall(cells[3]),
            )
        )

    if not header_seen:
        raise ClaimsRegisterError("claims register has no header row")
    if not alignment_seen:
        raise ClaimsRegisterError("claims register has no alignment row")
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
