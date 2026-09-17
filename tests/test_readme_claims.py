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
    pytest_node_ids,
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
