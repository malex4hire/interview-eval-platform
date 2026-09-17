"""RST-B1 — one command from clean clone to a completed evaluation.

The claim under test is reachability: a reviewer runs one command and gets a
live API carrying seed data whose evaluations already show the confidence
router's disposition. Nothing may be typed in between.

What this test covers, precisely: the tree is pristine (no virtualenv, no
database, no seeded audio — see tests/support/clean_tree.py), stdin is closed
so any prompt fails rather than blocks, and the assertions are made against
the HTTP interface rather than the database.

What it covers by default does NOT include the dependency-install leg: it
passes `DEMO_PYTHON` so the script reuses this suite's interpreter, which keeps
the test offline and quick. Setting IEP_DEMO_BARE=1 removes that shortcut and
makes the script build its own virtualenv and install its own requirements —
which is exactly what the `demo` job in .github/workflows/ci.yml does. Same
test, same assertions, one environment variable apart.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.support.clean_tree import materialise_clean_tree, repo_files

# Matches scripts/seed.py. The demo is credential-free by design; this is a
# fixture password for local demo data, not a secret.
SEED_ADMIN_EMAIL = "admin.acme@example.com"
SEED_PASSWORD = "demo-password-123"

ROUTING_DISPOSITIONS = {"correct", "incorrect", "requires_human_review"}

# Set IEP_DEMO_BARE=1 to make the script build its own virtualenv and install
# its own dependencies, which is what the CI `demo` job does. Off by default so
# the local suite stays offline and fast.
BARE = os.environ.get("IEP_DEMO_BARE") == "1"

# Installing dependencies dominates the bare run; reusing an interpreter does not.
BOOT_TIMEOUT_SECONDS = 420 if BARE else 120


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str, token: str | None = None, timeout: float = 10.0):
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict, token: str | None = None, timeout: float = 30.0):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(base: str, process: subprocess.Popen, log: Path) -> dict:
    deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(
                f"demo command exited early with code {process.returncode}\n"
                f"--- output ---\n{log.read_text(errors='replace')}"
            )
        try:
            return _get_json(f"{base}/health", timeout=3.0)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.5)
    pytest.fail(
        f"no /health response within {BOOT_TIMEOUT_SECONDS}s ({last_error})\n"
        f"--- output ---\n{log.read_text(errors='replace')}"
    )


@pytest.fixture(scope="module")
def demo_instance(tmp_path_factory) -> dict:
    """Run the one command against a pristine tree; yield connection details."""
    workspace = materialise_clean_tree(tmp_path_factory.mktemp("clean-clone"))
    entry_point = workspace / "demo.sh"
    assert entry_point.is_file(), (
        "RST-B1 requires a single committed entry point at ./demo.sh"
    )
    assert os.access(entry_point, os.X_OK), "./demo.sh must be executable"

    port = _free_port()
    log_path = workspace / "demo-output.log"
    environment = dict(os.environ)
    if BARE:
        # Nothing is handed to the script: it must find an interpreter, build a
        # virtualenv and install requirements on its own.
        environment.pop("DEMO_PYTHON", None)
    else:
        # Reuse this suite's interpreter so the test stays offline and quick.
        environment["DEMO_PYTHON"] = sys.executable
    # Any setting inherited from this suite's conftest or the developer's shell
    # would mask a missing default in the script, which is the thing under test.
    # PBKDF2_ITERATIONS is dropped too: the demo must be measured at its real
    # work factor, not at the suite's reduced one.
    for leaked in (
        "DATABASE_URL",
        "CONFIDENCE_THRESHOLD",
        "LLM_PROVIDER",
        "LLM_MODEL_NAME",
        "JWT_SECRET",
        "AUDIO_STORAGE_DIR",
        "PBKDF2_ITERATIONS",
        "ENVIRONMENT",
    ):
        environment.pop(leaked, None)

    with log_path.open("wb") as log_file:
        process = subprocess.Popen(
            ["./demo.sh", "--port", str(port)],
            cwd=workspace,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            # Closed stdin: a command that stops to ask a question gets EOF and
            # dies, which is how "no manual step" is enforced mechanically.
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=environment,
        )

    base = f"http://127.0.0.1:{port}"
    try:
        health = _wait_for_health(base, process, log_path)
        yield {
            "base": base,
            "health": health,
            "workspace": workspace,
            "log": log_path,
            "process": process,
        }
    finally:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=20)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.fixture(scope="module")
def admin_token(demo_instance) -> str:
    """Sign in as a seeded admin.

    A 401 here means the seed user does not exist, which means the command
    served an empty database. Named explicitly: the bare urllib error says
    "Unauthorized", which reads like an auth bug rather than the missing
    seed step it actually is.
    """
    try:
        login = _post_json(
            f"{demo_instance['base']}/auth/login",
            {"email": SEED_ADMIN_EMAIL, "password": SEED_PASSWORD},
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            pytest.fail(
                f"the seeded admin {SEED_ADMIN_EMAIL} does not exist, so the "
                "one command brought up an API with no demo data in it\n"
                f"--- output ---\n"
                f"{demo_instance['log'].read_text(errors='replace')}"
            )
        raise
    return login["access_token"]


def test_the_one_command_serves_a_live_instance(demo_instance):
    """A clean tree plus one command is a responding API."""
    health = demo_instance["health"]
    assert health["status"] == "ok"


def test_the_one_command_needs_no_api_credential(demo_instance):
    """The default provider is the deterministic offline one (RST-B1 SPEC)."""
    assert demo_instance["health"]["llm_provider"] == "mock"

    # A credential left in the environment would make the run look
    # credential-free while actually depending on one.
    for variable in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        assert not os.environ.get(variable), (
            f"{variable} is set in this environment; re-run without it so the "
            "credential-free claim is actually being tested"
        )


def test_a_seeded_evaluation_carries_a_routing_disposition(demo_instance, admin_token):
    """The interface probe: seed data arrives already evaluated and routed."""
    base = demo_instance["base"]
    interviews = _get_json(f"{base}/interviews", admin_token)
    assert interviews, "no seeded interviews reachable after the one command"

    evaluations = []
    for interview in interviews:
        for item in _get_json(f"{base}/interviews/{interview['id']}/responses", admin_token):
            current = item["current_evaluation"]
            if current is not None:
                evaluations.append(current)

    assert evaluations, "seed data contains no completed evaluation"
    for evaluation in evaluations:
        assert evaluation["verdict"] in ROUTING_DISPOSITIONS
        assert evaluation["llm_verdict"] in {"correct", "incorrect"}
        assert isinstance(evaluation["confidence_threshold"], int)


def test_the_seeded_run_includes_an_escalation(demo_instance, admin_token):
    """At least one seeded evaluation is routed to a human.

    Without this the demo shows a happy path only, which is the gap this ICG
    exists to close.
    """
    pending = _get_json(f"{demo_instance['base']}/reviews/pending", admin_token)
    assert pending, "no evaluation was routed to human review in the seed data"
    for evaluation in pending:
        assert evaluation["verdict"] == "requires_human_review"
        assert evaluation["confidence"] < evaluation["confidence_threshold"]


def test_the_command_asked_the_operator_for_nothing(demo_instance):
    """Closed stdin already enforces this; the log is the readable evidence."""
    output = demo_instance["log"].read_text(errors="replace")
    assert "EOFError" not in output
    assert "Traceback" not in output


def test_the_run_took_the_interpreter_path_it_was_asked_to(demo_instance):
    """The bare/shortcut distinction must be observable, not assumed.

    IEP_DEMO_BARE was the only thing separating the CI `demo` job from the
    bring-up test the matrix already runs three times, and nothing asserted the
    bare path was actually taken. Rename or drop the variable in a workflow
    edit and the fixture quietly takes the shortcut branch, every assertion
    passes, and ci.yml's claim that this job "proves a bare runner works" stops
    being true with nothing red.

    demo.sh already prints which path it took; this reads it.
    """
    output = demo_instance["log"].read_text(errors="replace")
    if BARE:
        assert "creating .venv" in output, (
            "IEP_DEMO_BARE=1 but the script did not build its own virtualenv; "
            f"the install leg was not exercised\n--- output ---\n{output}"
        )
        assert "using the interpreter named by DEMO_PYTHON" not in output
    else:
        assert "using the interpreter named by DEMO_PYTHON" in output, (
            "expected the offline shortcut, but the script did not take it\n"
            f"--- output ---\n{output}"
        )


def test_the_clean_tree_carries_no_prebuilt_state():
    """Guards the fixture itself.

    If the copied file set included a virtualenv, a database or seeded audio,
    every assertion above would be proving something about this developer's
    box rather than about a clean clone. Asserted against the source file list,
    not the workspace, which the run has since written to.
    """
    manifest = repo_files()
    assert manifest, "clean-tree manifest is empty"

    top_level = {Path(entry).parts[0] for entry in manifest}
    assert ".venv" not in top_level
    assert "__pycache__" not in top_level
    assert not [entry for entry in manifest if entry.endswith(".db")]
    # storage/audio/.gitkeep is tracked; seeded transcripts are not.
    assert [entry for entry in manifest if entry.startswith("storage/audio/")] == [
        "storage/audio/.gitkeep"
    ]
    assert "demo.sh" in manifest
