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


@pytest.fixture(scope="module")
def collected_node_ids() -> set[str]:
    """Every test pytest can actually collect, as node ids.

    Collected rather than imported: a test that fails to import, or that a
    conftest deselects, is not in the suite however much it looks like it is.
    """
    completed = subprocess.run(
        # `-o addopts=` clears the project's own addopts first. Without it the
        # `-q` already configured there combines with this one into `-qq`, and
        # pytest prints per-file counts instead of node ids — which made every
        # binding look absent.
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "--collect-only",
            "-q",
            "--no-header",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, (
        f"collection failed\n--- stdout ---\n{completed.stdout}\n"
        f"--- stderr ---\n{completed.stderr}"
    )
    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if "::" in line and not line.startswith(" ")
    }


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


def test_no_named_gate_is_skipped():
    """Fails if a claim names a skipped gate.

    A skip marker on a bound test is the quiet version of deleting it: the
    suite stays green and the claim stops being proven.
    """
    offenders: list[str] = []
    for node_id in pytest_node_ids():
        path, _, test_name = node_id.partition("::")
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        match = re.search(
            rf"((?:^@.*\n)*)^def {re.escape(test_name)}\b", source, flags=re.MULTILINE
        )
        if match and re.search(r"@pytest\.mark\.(skip|skipif|xfail)", match.group(1)):
            offenders.append(node_id)
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


def test_every_row_present_in_the_register_is_parsed():
    """The structural property: rows-parsed == rows-present.

    Asserted on the real README, not on a fixture. A parser that skips a line
    it cannot read keeps reporting green while checking fewer claims than the
    register appears to hold — the same shape as a README table that five green
    CI jobs never looked at. Header row + alignment row + one line per claim
    must account for every non-blank line between the markers.
    """
    present = len(register_rows())
    accounted = len(CLAIMS) + 2  # header, alignment
    assert accounted == present, (
        f"the register has {present} non-blank lines but {accounted} were "
        f"accounted for; {present - accounted} row(s) are checked by nothing"
    )


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

_CAP_PATTERN = re.compile(r"^REGISTER_CAP\s*=\s*(\d+)", re.MULTILINE)
CAP_SOURCE = "scripts/readme_claims.py"
DECISION_LOG = "docs/DECISIONS.md"


def cap_at(revision: str, repo: Path = REPO_ROOT) -> int | None:
    """The cap as of a revision, or None if it did not exist yet."""
    shown = git("show", f"{revision}:{CAP_SOURCE}", repo=repo)
    if shown.returncode != 0:
        return None
    match = _CAP_PATTERN.search(shown.stdout)
    return int(match.group(1)) if match else None


def cap_raises_without_a_decision(
    revision_range: str | None, repo: Path = REPO_ROOT
) -> list[str]:
    """SHAs in the range that raise the cap without logging a decision.

    Introducing the cap is not a raise — there is nothing to raise from — so a
    commit whose parent has no cap is skipped. Lowering it is not a raise
    either: tightening a bound needs no permission.
    """
    offenders: list[str] = []
    for sha in commits(revision_range, repo=repo):
        after = cap_at(sha, repo=repo)
        before = cap_at(f"{sha}^", repo=repo)
        if after is None or before is None or after <= before:
            continue

        touched = git(
            "show", "--name-only", "--format=", sha, repo=repo
        ).stdout.split()
        if DECISION_LOG not in touched:
            offenders.append(sha)
            continue

        # "Corresponding" is load-bearing: touching the file is not the same as
        # recording the decision, so the new value has to appear in what the
        # commit ADDED to the log.
        diff = git("show", sha, "--", DECISION_LOG, repo=repo).stdout
        added = [
            line for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        if not any(str(after) in line for line in added):
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
            handle.write(f"\n### Cap raised to {cap}\n\nBecause of a reason.\n")
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


def test_lowering_the_cap_needs_no_decision(tmp_path):
    """Tightening a bound needs no permission; only loosening one does."""
    repo = _cap_repo(tmp_path / "lowered", 20)
    _raise_cap(repo, 19, log_decision=False, subject="drop a claim, lower the cap")

    assert not cap_raises_without_a_decision(None, repo=repo)
