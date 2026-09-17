"""RST-B3 — every capability claim in the README names the gate that proves it.

D-3: "a green check isn't proof" applies to prose as well as CI. A README is
the most-read file in a repository and the least tested one; this module makes
its claims fail like code.

The division of labour, stated plainly because it is the honest scope:

  * these tests prove each registered claim's gate RESOLVES — the artifact path
    exists, the node id is really in the collected suite, and it carries no
    skip marker. That is the failure mode CI cannot see: a renamed or deleted
    test just stops running, and a green suite says nothing about it.
  * the `claims` job in .github/workflows/ci.yml proves each gate PASSES, by
    running exactly the node ids this register names. test_ci_runs_every...
    below asserts that job still exists, so the passing half cannot be quietly
    dropped either.

What is NOT mechanised: noticing a new capability claim written into the prose
and never added to the register. The register is curated. A prose scanner was
considered and rejected — it would have to guess what a "claim" is, and a
check with false positives gets disabled by whoever trusts it next.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from scripts.readme_claims import (
    BEGIN,
    END,
    Claim,
    ClaimsRegisterError,
    parse_claims,
    REGISTER_CAP,
    pytest_node_ids,
    register_rows,
)
from tests.support.git_range import (
    RangeUnresolvable,
    commits,
    git,
    resolve_range,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

try:
    CLAIMS: list[Claim] = parse_claims()
except ClaimsRegisterError:
    # Degrade to a clean failure rather than a collection error: an exception
    # here would take the whole suite down instead of reporting the one thing
    # that is actually wrong. test_the_register_exists_and_is_delimited is what
    # turns this empty list into a loud failure.
    CLAIMS = []


def _collect(*extra_args: str) -> set[str]:
    """Node ids pytest actually collects, optionally filtered by marker.

    `-o addopts=` clears the project's own options first. Without it the `-q`
    already configured there combines with this one into `-qq`, and pytest
    prints per-file counts instead of node ids — which made every binding look
    absent.
    """
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "-o", "addopts=", "--collect-only", "-q", "--no-header", *extra_args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, (
        f"collection failed for {extra_args}\n--- stdout ---\n{completed.stdout}\n"
        f"--- stderr ---\n{completed.stderr}"
    )
    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if "::" in line and not line.startswith(" ")
    }


@pytest.fixture(scope="module")
def collected_node_ids() -> set[str]:
    """Every test pytest can collect.

    Collected rather than imported: a test that fails to import, or that a
    conftest deselects, is not in the suite however much it looks like it is.
    """
    return _collect()


@pytest.fixture(scope="module")
def skip_marked_node_ids(collected_node_ids) -> set[str]:
    """Node ids carrying a skip/skipif/xfail marker.

    Derived by asking PYTEST — collect everything, collect again deselecting
    those markers, and diff. The previous implementation regexed the source for
    `^@...` lines above `^def <name>`, which only recognised the one shape the
    author happened to write. Measured misses: a decorator split across lines
    (what black and ruff produce), a module-level `pytestmark`, and a blank
    line between decorator and def. Each really skipped the gate and each read
    as clean, because a failed regex was treated as "no offence" — evidence
    unavailable scored as property satisfied.
    """
    return collected_node_ids - _collect("-m", "not skip and not skipif and not xfail")


def workflow_jobs(workflow: Path) -> list[str]:
    """Job names defined by a workflow.

    Scoped to the `jobs:` block rather than matched across the whole file: at
    top level `on:` also has two-space children, so a looser pattern reports
    `push` as a job and would accept a claim binding to it.
    """
    text = workflow.read_text(encoding="utf-8")
    match = re.search(r"^jobs:\s*$", text, flags=re.MULTILINE)
    if not match:
        return []
    block = text[match.end() :]
    # Stop at the next top-level key, if there is one.
    end = re.search(r"^\S", block, flags=re.MULTILINE)
    if end:
        block = block[: end.start()]
    return re.findall(r"^  ([\w-]+):", block, flags=re.MULTILINE)


def _claim_ids() -> list[str]:
    return [claim.identifier for claim in CLAIMS]


def test_the_register_exists_and_is_delimited():
    markdown = README.read_text(encoding="utf-8")
    assert BEGIN in markdown and END in markdown
    assert CLAIMS, "the claims register is empty"


def test_claim_identifiers_are_unique():
    identifiers = _claim_ids()
    duplicates = {i for i in identifiers if identifiers.count(i) > 1}
    assert not duplicates, f"duplicate claim identifiers: {sorted(duplicates)}"


@pytest.mark.parametrize("claim", CLAIMS, ids=_claim_ids())
def test_every_claim_states_something(claim: Claim):
    assert len(claim.text) > 20, f"{claim.identifier} says too little to be a claim"


@pytest.mark.parametrize("claim", CLAIMS, ids=_claim_ids())
def test_every_claim_names_an_artifact_that_exists(claim: Claim):
    """SPEC: claim text and identifier both resolve to repository artifacts."""
    assert claim.artifacts, f"{claim.identifier} names no implementing artifact"
    for artifact in claim.artifacts:
        assert (REPO_ROOT / artifact).exists(), (
            f"{claim.identifier} points at {artifact}, which is not in the repository"
        )


@pytest.mark.parametrize("claim", CLAIMS, ids=_claim_ids())
def test_every_claim_names_at_least_one_gate(claim: Claim):
    assert claim.gates, f"{claim.identifier} names no gate, so nothing proves it"
    unrecognised = set(claim.gates) - set(claim.pytest_gates) - set(claim.workflow_gates)
    assert not unrecognised, (
        f"{claim.identifier} names gates in no recognised form: {sorted(unrecognised)}. "
        "Use tests/<file>.py::<test> or .github/workflows/<file>.yml::<job>."
    )


@pytest.mark.parametrize("claim", CLAIMS, ids=_claim_ids())
def test_every_named_pytest_gate_is_in_the_suite(claim: Claim, collected_node_ids):
    """Fails on an absent or renamed gate — the failure CI cannot see."""
    for gate in claim.pytest_gates:
        assert gate in collected_node_ids, (
            f"{claim.identifier} names {gate}, which pytest does not collect. "
            "It was renamed, deleted, or it fails to import."
        )


@pytest.mark.parametrize("claim", CLAIMS, ids=_claim_ids())
def test_every_named_workflow_gate_is_a_real_job(claim: Claim):
    for gate in claim.workflow_gates:
        workflow_path, job = gate.split("::")
        workflow = REPO_ROOT / workflow_path
        assert workflow.is_file(), f"{claim.identifier} names missing {workflow_path}"
        jobs = workflow_jobs(workflow)
        assert job in jobs, (
            f"{claim.identifier} names job {job!r} in {workflow_path}; "
            f"that workflow defines {sorted(jobs)}"
        )


def test_no_named_gate_is_skipped(skip_marked_node_ids):
    """Fails if a claim names a skipped gate.

    A skip marker on a bound test is the quiet version of deleting it: the
    suite stays green and the claim stops being proven. Any of skip, skipif or
    xfail disqualifies a gate — a conditionally-skipped test does not reliably
    prove the claim that cites it.
    """
    offenders = sorted(set(pytest_node_ids()) & skip_marked_node_ids)
    assert not offenders, f"claims are bound to skipped gates: {offenders}"


def test_ci_runs_every_gate_the_register_names():
    """The passing half of RST-B3 must stay wired.

    These tests prove the gates resolve; the CI job proves they pass. If that
    job is removed, "and passes" silently stops being enforced — so its absence
    is a test failure here.
    """
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts.readme_claims" in workflow_text, (
        "no CI step derives its node ids from the claims register; the gates "
        "named by the README would no longer be required to pass"
    )
    jobs = workflow_jobs(WORKFLOW)
    assert "claims" in jobs, f"ci.yml has no `claims` job; found {sorted(jobs)}"


def test_the_register_is_not_empty():
    """The parametrised tests below vanish on an empty register.

    pytest reports a parametrisation over [] as a skip, so an emptied register
    would silently remove most of this module. This is the check that stays
    behind to notice.
    """
    assert CLAIMS, (
        "the claims register parsed to nothing; every per-claim check above "
        "would have been skipped rather than run"
    )


# There used to be a test_every_row_present_in_the_register_is_parsed here,
# asserting len(CLAIMS) + 2 == len(register_rows()). It was removed along with
# the arithmetic check inside parse_claims that it mirrored: both recomputed the
# same identity from the same parser, so neither could fail. Deleting the parser
# block turned nothing red — the definition of decoration. The refusals below
# are the real mechanism, and the parametrised cases that follow are what prove
# it.

@pytest.mark.parametrize(
    "label,row",
    [
        # Still valid GitHub-flavoured markdown, and a completely ordinary edit.
        ("leading pipe dropped", "C9 | A claim | `demo.sh` | `tests/t.py::test_a` |"),
        ("identifier left blank", "|  | A claim | `demo.sh` | `tests/t.py::test_a` |"),
        ("stray prose between the markers", "TODO: put the chain claim back"),
        ("pipe inside a cell", "| C9 | A | claim | `demo.sh` | `tests/t.py::test_a` |"),
    ],
)
def test_no_unparseable_row_is_skipped(tmp_path, label, row):
    """Every way a row can be unreadable must FAIL the gate, not vanish.

    Each of these four was silently dropped before: the register parsed, the
    suite went green, and one fewer claim was being checked than the README
    displayed. Parametrised over shapes a real editor produces rather than the
    single case the first fix happened to handle.
    """
    register = tmp_path / f"{label.replace(' ', '-')}.md"
    register.write_text(
        f"""{BEGIN}
| # | Claim | Implemented in | Proven by |
|---|---|---|---|
| C1 | A well-formed claim | `demo.sh` | `tests/t.py::test_a` |
{row}
{END}
""",
        encoding="utf-8",
    )
    with pytest.raises(ClaimsRegisterError):
        parse_claims(register)


@pytest.mark.parametrize(
    "label,extra",
    [
        ("duplicate alignment row", "|---|---|---|---|"),
        ("duplicate header row", "| # | Claim | Implemented in | Proven by |"),
    ],
)
def test_a_repeated_structure_row_is_refused(tmp_path, label, extra):
    """Review finding 6, pinned.

    The one-shot guards meant a SECOND header or alignment row fell through to
    the claim branch and registered as a claim named '#' or '---'. Both are
    ordinary copy-paste edits and both used to parse green. On the real README
    the phantom row carries no gate, so the failure eventually surfaced — but
    as "claim '---' names no gate" rather than as the register damage it is.
    """
    register = tmp_path / f"{label.replace(' ', '-')}.md"
    register.write_text(
        f"""{BEGIN}
| # | Claim | Implemented in | Proven by |
|---|---|---|---|
| C1 | A well-formed claim | `demo.sh` | `tests/t.py::test_a` |
{extra}
| C2 | Another claim | `demo.sh` | `tests/t.py::test_b` |
{END}
""",
        encoding="utf-8",
    )
    with pytest.raises(ClaimsRegisterError, match="second"):
        parse_claims(register)


def test_a_register_without_its_header_is_refused(tmp_path):
    """A markdown table needs a header to render at all; a register missing one
    is damaged, and damage is refused rather than partially read."""
    register = tmp_path / "headerless.md"
    register.write_text(
        f"""{BEGIN}
| C1 | A claim | `demo.sh` | `tests/t.py::test_a` |
{END}
""",
        encoding="utf-8",
    )
    with pytest.raises(ClaimsRegisterError, match="header"):
        parse_claims(register)


def test_a_malformed_row_raises_instead_of_vanishing(tmp_path):
    """A claim row that does not split into four cells must be loud.

    This is the fail-open the parser had: a '|' inside a claim's text splits
    the row into five cells, the row was skipped, and the claim quietly left
    the register while every downstream check went on passing — because none
    of them knew it had ever been there.
    """
    from scripts.readme_claims import ClaimsRegisterError

    register = tmp_path / "extra-pipe.md"
    register.write_text(
        f"""{BEGIN}
| # | Claim | Implemented in | Proven by |
|---|---|---|---|
| C1 | Routing happens | correctly | `app/x.py` | `tests/test_x.py::test_y` |
{END}
""",
        encoding="utf-8",
    )
    with pytest.raises(ClaimsRegisterError) as raised:
        parse_claims(register)
    assert "4 cells" in str(raised.value)


def test_the_remedy_that_error_recommends_actually_works(tmp_path):
    """An error message that recommends a fix owes proof the fix works.

    The first version of the message above suggested a backslash escape. It
    does not help: the row is split on '|' before any markdown escaping is
    considered, so the advice would have sent the next reader in a circle.
    """
    from scripts.readme_claims import ClaimsRegisterError

    register = tmp_path / "entity.md"
    register.write_text(
        f"""{BEGIN}
| # | Claim | Implemented in | Proven by |
|---|---|---|---|
| C1 | Routing happens &#124; correctly | `demo.sh` | `tests/test_x.py::test_y` |
{END}
""",
        encoding="utf-8",
    )
    claims = parse_claims(register)
    assert len(claims) == 1
    assert "&#124;" in claims[0].text

    # And the advice the message rejects really is no good.
    backslash = tmp_path / "backslash.md"
    backslash.write_text(
        f"""{BEGIN}
| # | Claim | Implemented in | Proven by |
|---|---|---|---|
| C1 | Routing happens \\| correctly | `demo.sh` | `tests/test_x.py::test_y` |
{END}
""",
        encoding="utf-8",
    )
    with pytest.raises(ClaimsRegisterError):
        parse_claims(backslash)


def test_the_parser_rejects_a_damaged_register(tmp_path):
    """Mutation check on the reader itself.

    If a malformed register parsed as "no claims", every check above would
    pass vacuously — the classic check that cannot fire.
    """
    without_markers = tmp_path / "no-register.md"
    without_markers.write_text("# Title\n\nNo register here.\n", encoding="utf-8")
    with pytest.raises(ClaimsRegisterError):
        parse_claims(without_markers)

    empty_register = tmp_path / "empty-register.md"
    empty_register.write_text(f"{BEGIN}\n\n{END}\n", encoding="utf-8")
    with pytest.raises(ClaimsRegisterError):
        parse_claims(empty_register)


# ---------------------------------------------------------------------------
# RST-B5 — the register is bounded
# ---------------------------------------------------------------------------

# Tolerates an annotated or spaced assignment. Broadening the pattern is the
# convenience; CapUnreadable below is the actual protection.
_CAP_PATTERN = re.compile(r"^REGISTER_CAP\s*(?::[^=\n]+)?=\s*(\d+)", re.MULTILINE)
CAP_SOURCE = "scripts/readme_claims.py"
DECISION_LOG = "docs/DECISIONS.md"

# A decision entry must name the constant and the new value on one line, e.g.
#   REGISTER_CAP raised to 21
# A bare digit match is not enough: docs/DECISIONS.md carries dated headings,
# so "## 2026-09-21 ..." would launder a raise to 21 all by itself.
def _mentions_the_raise(added_lines: list[str], new_cap: int) -> bool:
    return any(
        "REGISTER_CAP" in line and str(new_cap) in line for line in added_lines
    )


class CapReading(NamedTuple):
    """What could be read at a revision, and whether reading succeeded.

    Three states, kept apart because they mean different things:
      absent      — the file does not exist at this revision
      unreadable  — the file exists but carries no parseable REGISTER_CAP
      ok          — a value was read

    Collapsing `unreadable` into `absent` is what let an annotated assignment
    disarm the whole check: `REGISTER_CAP: int = 21` stopped matching, the
    caller read that as "nothing to compare", and the raise sailed through.
    """

    state: str  # "absent" | "unreadable" | "ok"
    value: int | None


def cap_at(revision: str, repo: Path = REPO_ROOT) -> CapReading:
    shown = git("show", f"{revision}:{CAP_SOURCE}", repo=repo)
    if shown.returncode != 0:
        return CapReading("absent", None)
    match = _CAP_PATTERN.search(shown.stdout)
    if match is None:
        return CapReading("unreadable", None)
    return CapReading("ok", int(match.group(1)))


def _exempted_shas(repo: Path = REPO_ROOT) -> set[str]:
    """SHAs the decision log retrospectively accounts for.

    RST-B5's check is a property of history, and after the branch merges the
    full-history fallback re-finds an old violation on every run forever. This
    repository never rewrites history, so without a forward remedy the only
    exits would be a permanently red suite or a rewrite. Naming the SHA in the
    decision log is that remedy: late, logged, reviewable, and it leaves the
    original commit untouched.
    """
    log_path = repo / DECISION_LOG
    if not log_path.is_file():
        return set()
    text = log_path.read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in re.finditer(
            r"REGISTER_CAP[^\n]*\b([0-9a-f]{7,40})\b", text
        )
    }


def cap_raises_without_a_decision(
    revision_range: str | None, repo: Path = REPO_ROOT
) -> list[str]:
    """SHAs in the range that raise the cap without logging a decision.

    Introducing the cap is not a raise — there is nothing to raise from — so a
    commit whose parent has no cap is skipped. Lowering it is not a raise
    either: tightening a bound needs no permission. A commit that makes an
    existing cap unreadable IS an offender: that is how the gate gets disarmed.
    """
    offenders: list[str] = []
    exempted = _exempted_shas(repo=repo)

    for sha in commits(revision_range, repo=repo):
        if any(sha.startswith(prefix) for prefix in exempted):
            continue
        # Four cases, and only one of them is an attack.
        #
        #   parent ok  -> child unreadable : the cap was removed or reformatted.
        #                                    This is the disarm case. OFFENDER.
        #   parent not ok -> child unreadable : the commit predates the cap. Skip.
        #   parent not ok -> child ok      : the cap was introduced here. Skip.
        #   both ok                        : compare the values.
        #
        # Reading "unreadable" as "no cap" in all four collapsed the first case
        # into the second and disabled the gate; reading it as an offence in all
        # four flagged every commit written before the cap existed.
        after = cap_at(sha, repo=repo)
        before = cap_at(f"{sha}^", repo=repo)

        if after.state == "unreadable":
            if before.state == "ok":
                offenders.append(sha)
            continue
        if after.state == "absent" or before.state != "ok":
            continue
        if after.value <= before.value:
            continue

        touched = git(
            "show", "--name-only", "--format=", sha, repo=repo
        ).stdout.split()
        if DECISION_LOG not in touched:
            offenders.append(sha)
            continue

        diff = git("show", sha, "--", DECISION_LOG, repo=repo).stdout
        added = [
            line for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        if not _mentions_the_raise(added, after.value):
            offenders.append(sha)
    return offenders


def test_the_register_is_within_its_cap():
    """SPEC: the register does not grow without a decision."""
    assert len(CLAIMS) <= REGISTER_CAP, (
        f"the claims register holds {len(CLAIMS)} claims against a cap of "
        f"{REGISTER_CAP}. Raise REGISTER_CAP in {CAP_SOURCE} and record why in "
        f"{DECISION_LOG}, in the same commit — or do not add the claim."
    )


def test_the_cap_matches_the_register_it_bounds():
    """A cap far above the register would be a cap in name only.

    RST-B5 sets it to the registered count, so drift in either direction is a
    finding: claims removed without lowering it leaves silent headroom.
    """
    assert REGISTER_CAP == len(CLAIMS), (
        f"REGISTER_CAP is {REGISTER_CAP} but the register holds {len(CLAIMS)}. "
        "The cap is set to the registered count; move them together, with a "
        f"decision entry in {DECISION_LOG} when the cap goes up."
    )


def test_no_commit_raises_the_cap_without_a_decision_entry():
    """SPEC: raising the cap requires a decision log entry in the same commit."""
    try:
        resolution = resolve_range()
    except RangeUnresolvable as exc:
        pytest.fail(f"RST-B5 cannot establish a commit range to check: {exc}")

    offenders = cap_raises_without_a_decision(resolution.revision_range)
    assert not offenders, (
        f"these commits raise REGISTER_CAP with no corresponding entry in "
        f"{DECISION_LOG}: {offenders}"
    )


def _cap_repo(path: Path, cap: int) -> Path:
    """A miniature repository carrying a cap and a decision log."""
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "work", str(path), repo=path.parent)
    git("config", "user.name", "test", repo=path)
    git("config", "user.email", "test@example.invalid", repo=path)
    (path / "scripts").mkdir(exist_ok=True)
    (path / "docs").mkdir(exist_ok=True)
    (path / CAP_SOURCE).write_text(f"REGISTER_CAP = {cap}\n", encoding="utf-8")
    (path / DECISION_LOG).write_text("# Decisions\n", encoding="utf-8")
    git("add", "-A", repo=path)
    git("commit", "-q", "-m", "RST-B5: set the cap", repo=path)
    return path


def _raise_cap(repo: Path, cap: int, *, log_decision: bool, subject: str) -> None:
    (repo / CAP_SOURCE).write_text(f"REGISTER_CAP = {cap}\n", encoding="utf-8")
    if log_decision:
        with (repo / DECISION_LOG).open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n### Cap raised\n\nREGISTER_CAP raised to {cap} because of a reason.\n"
            )
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", subject, repo=repo)


def test_a_cap_raise_without_a_decision_is_caught(tmp_path):
    """The detector's red case, on a repository built for it."""
    repo = _cap_repo(tmp_path / "unlogged", 20)
    _raise_cap(repo, 21, log_decision=False, subject="sneak one more claim in")

    assert cap_raises_without_a_decision(None, repo=repo), (
        "a commit raised the cap with no decision entry and was not caught"
    )


def test_a_cap_raise_with_a_decision_is_allowed(tmp_path):
    """The detector's green case. Without this the check could be a constant
    'fail' and the red case above would not notice."""
    repo = _cap_repo(tmp_path / "logged", 20)
    _raise_cap(repo, 21, log_decision=True, subject="RST-B5: raise the cap to 21")

    assert not cap_raises_without_a_decision(None, repo=repo)


def test_touching_the_decision_log_is_not_recording_a_decision(tmp_path):
    """"Corresponding" means the entry is about this raise.

    Editing the log for some unrelated reason in the same commit must not
    launder a cap raise — otherwise the requirement degrades into "remember to
    touch two files".
    """
    repo = _cap_repo(tmp_path / "unrelated", 20)
    (repo / CAP_SOURCE).write_text("REGISTER_CAP = 21\n", encoding="utf-8")
    with (repo / DECISION_LOG).open("a", encoding="utf-8") as handle:
        handle.write("\n### Something else entirely\n\nUnrelated note.\n")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "raise the cap, mention nothing", repo=repo)

    assert cap_raises_without_a_decision(None, repo=repo)


def test_a_reformatted_assignment_cannot_disarm_the_detector(tmp_path):
    """Review finding 1, pinned.

    `REGISTER_CAP: int = 21` stopped matching the pattern, `cap_at` returned
    "no cap", and the caller read that as "nothing to compare" — so an
    annotated assignment silently switched the whole gate off while the suite
    stayed green. Measured before the fix: offenders == [].
    """
    repo = _cap_repo(tmp_path / "annotated", 20)
    (repo / CAP_SOURCE).write_text("REGISTER_CAP: int = 21\n", encoding="utf-8")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "annotate the constant, raise it, log nothing", repo=repo)

    assert cap_raises_without_a_decision(None, repo=repo), (
        "an annotated assignment disarmed the cap detector"
    )


def test_an_annotated_assignment_is_still_read_when_it_is_honest(tmp_path):
    """The inverse row: broadening the pattern must not break a real raise.

    Without this, refusing every annotated form would look identical to
    reading it correctly.
    """
    repo = _cap_repo(tmp_path / "annotated-ok", 20)
    (repo / CAP_SOURCE).write_text("REGISTER_CAP: int = 21\n", encoding="utf-8")
    with (repo / DECISION_LOG).open("a", encoding="utf-8") as handle:
        handle.write("\n### Cap raised\n\nREGISTER_CAP raised to 21 for a reason.\n")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "RST-B5: raise to 21", repo=repo)

    assert cap_at("HEAD", repo=repo).value == 21
    assert not cap_raises_without_a_decision(None, repo=repo)


def test_removing_the_cap_entirely_is_an_offence(tmp_path):
    """Deleting the constant is the blunt version of reformatting it."""
    repo = _cap_repo(tmp_path / "deleted", 20)
    (repo / CAP_SOURCE).write_text("# the cap used to live here\n", encoding="utf-8")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "drop the cap", repo=repo)

    assert cap_raises_without_a_decision(None, repo=repo)


def test_commits_predating_the_cap_are_not_offenders(tmp_path):
    """The other side of "unreadable".

    Every commit written before the cap existed has the source file but no
    constant in it. Treating that as an offence flagged the entire history —
    measured, seven commits on this branch — so the state has to be read
    against the PARENT, not in isolation.
    """
    repo = tmp_path / "predating"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    git("init", "-q", "-b", "work", str(repo), repo=repo.parent)
    git("config", "user.name", "test", repo=repo)
    git("config", "user.email", "test@example.invalid", repo=repo)
    (repo / CAP_SOURCE).write_text("# no cap yet\n", encoding="utf-8")
    (repo / DECISION_LOG).write_text("# Decisions\n", encoding="utf-8")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "work that predates the cap", repo=repo)
    (repo / CAP_SOURCE).write_text("REGISTER_CAP = 20\n", encoding="utf-8")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "RST-B5: introduce the cap", repo=repo)

    assert not cap_raises_without_a_decision(None, repo=repo)


@pytest.mark.parametrize(
    "entry",
    [
        "\n## 2026-09-21 — something else entirely\n\nUnrelated.\n",
        "\n### D-9 — chose sqlite (2026)\n\nUnrelated.\n",
    ],
)
def test_a_dated_entry_does_not_launder_a_raise(tmp_path, entry):
    """Review finding 2, pinned.

    The check used to search the added lines for the digits of the new cap
    anywhere. This log is written with dated headings, so ordinary entries
    already contain those digits and satisfied it by accident — both of these
    were measured passing. The entry must now name REGISTER_CAP and the value
    on one line.
    """
    repo = _cap_repo(tmp_path / f"dated{abs(hash(entry)) % 1000}", 20)
    (repo / CAP_SOURCE).write_text("REGISTER_CAP = 21\n", encoding="utf-8")
    with (repo / DECISION_LOG).open("a", encoding="utf-8") as handle:
        handle.write(entry)
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "raise the cap under cover of a date", repo=repo)

    assert cap_raises_without_a_decision(None, repo=repo)


def test_an_unlogged_raise_that_reached_main_can_be_cleared_forward(tmp_path):
    """Review finding 5 — the recovery path, in the shape that actually occurs.

    RST-B5 is a property of commits, so once an unlogged raise merges, the
    post-merge full-history fallback re-finds it on every run forever. A later
    commit adding an ordinary decision entry does NOT clear it: the check reads
    the offending commit's own diff. In a repository whose standing rule is
    `git revert`, never rewrite, that would leave only a permanently red suite
    or a history rewrite — and a gate with no recovery gets deleted by whoever
    hits it, not repaired.

    The remedy is to name the offending SHA on a REGISTER_CAP line in the
    decision log. `_exempted_shas` reads that file from the working tree at
    HEAD rather than from any commit's diff, which is precisely what lets a
    LATER commit supply it.

    Reproduced here through a real merge, against the real range resolver, so
    the range under test is the full-history fallback a merged branch actually
    gets — not a convenient linear fixture.
    """
    repo = tmp_path / "merged"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    git("init", "-q", "-b", "main", str(repo), repo=repo.parent)
    git("config", "user.name", "test", repo=repo)
    git("config", "user.email", "test@example.invalid", repo=repo)
    (repo / CAP_SOURCE).write_text("REGISTER_CAP = 20\n", encoding="utf-8")
    (repo / DECISION_LOG).write_text("# Decisions\n", encoding="utf-8")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "RST-B5: introduce the cap", repo=repo)

    # A branch raises the cap, logs nothing, and merges.
    git("checkout", "-q", "-b", "sneaky", repo=repo)
    (repo / CAP_SOURCE).write_text("REGISTER_CAP = 21\n", encoding="utf-8")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "add a 21st claim", repo=repo)
    git("checkout", "-q", "main", repo=repo)
    git("merge", "-q", "--no-ff", "sneaky", "-m", "Merge sneaky", repo=repo)

    resolution = resolve_range(repo)
    assert resolution.source == "full-history", (
        "fixture precondition: a merged branch must reach the full-history "
        f"fallback, got {resolution.source}"
    )

    offenders = cap_raises_without_a_decision(resolution.revision_range, repo=repo)
    assert offenders, "fixture precondition: the merged raise must be an offence"
    offending_sha = offenders[0]

    # A later entry that names the constant and the value but NOT the sha is
    # not a remedy. Otherwise "recovery" would just be laundering with an extra
    # step, and any vague note would clear any violation.
    with (repo / DECISION_LOG).open("a", encoding="utf-8") as handle:
        handle.write("\n### Cap\n\nREGISTER_CAP raised to 21 at some point.\n")
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "note the cap change vaguely", repo=repo)
    assert cap_raises_without_a_decision(
        resolve_range(repo).revision_range, repo=repo
    ) == offenders, "a decision entry without the sha must not clear the violation"

    # Naming the sha does clear it.
    with (repo / DECISION_LOG).open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n### Retrospective\n\nREGISTER_CAP raised to 21 in {offending_sha}, "
            "accepted after the fact.\n"
        )
    git("add", "-A", repo=repo)
    git("commit", "-q", "-m", "RST-B5: account for an earlier raise", repo=repo)

    assert not cap_raises_without_a_decision(
        resolve_range(repo).revision_range, repo=repo
    ), "naming the offending sha in the decision log did not clear the gate"


def test_lowering_the_cap_needs_no_decision(tmp_path):
    """Tightening a bound needs no permission; only loosening one does."""
    repo = _cap_repo(tmp_path / "lowered", 20)
    _raise_cap(repo, 19, log_decision=False, subject="drop a claim, lower the cap")

    assert not cap_raises_without_a_decision(None, repo=repo)
