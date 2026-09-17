# Decisions

Design decisions taken while working on this repository, and why. The
product-level decisions (D-1 … D-8) live in README.md; this file records the
ones made during later work on the repository itself.

## 2026-09-17 — reachability and evidence (RST-B1 … RST-B5)

### B1-a — the one command is a shell script, not a compose stack

`./demo.sh` rather than `docker compose up`. The application is SQLite-backed
with no external service, so a compose stack would add a Docker requirement to
a demo that otherwise needs only Python. The script is also what CI runs, so
the documented path and the proven path are the same path.

Rejected: a Makefile target (adds `make` without removing anything) and a
compose file (a second, untested entry point is a claim nothing checks).

### B1-b — re-running the demo never reseeds or resets

`scripts/seed.py --if-empty` exits without writing when the database already
holds an evaluation. The obvious alternative — always `--reset` — would make
the demo idempotent by destroying data, which contradicts the repository's own
versioning rule that a recorded verdict is never overwritten. `--reset` remains
available and explicit.

### B1-c — the bring-up test reuses the suite's interpreter; CI does not

`DEMO_PYTHON` lets the test skip virtualenv creation and dependency install, so
the local suite stays offline and quick. That leaves the install leg unproven,
so the same test runs again in CI under `IEP_DEMO_BARE=1`, where the script has
to find an interpreter, build a virtualenv and install requirements itself.

One test, two environments, and the gap between them stated in the test's own
docstring rather than left for a reader to discover.

### B1-d — "clean clone" is built from `git ls-files`, not `git clone`

A clone carries only committed state, so a test written before its
implementation could never pass, and test-first would have been impossible. The
tree is rebuilt from `git ls-files --cached --others --exclude-standard`:
tracked files plus untracked files that are not ignored. Because `.venv/`,
`*.db`, `__pycache__/` and `storage/audio/*` are all gitignored, the copy has
no virtualenv, no database and no seeded artefacts.

`test_the_clean_tree_carries_no_prebuilt_state` asserts that, so the fixture
cannot quietly start proving something about the developer's machine.

### B2-a — the artifact is an SVG this repository generates itself

No asciinema, no terminal recorder, no headless browser, no image library. The
renderer is stdlib string formatting in `scripts/escalation_artifact.py`, which
keeps regeneration dependency-free and byte-deterministic, and keeps the
artifact readable as text in a diff.

Rejected: a PNG (binary diffs, needs a rasteriser, harder to make
reproducible) and a third-party asciicast embed (RST-B2 forbids external
hosting outright).

### B2-b — recording and rendering are separate, and both are committed

`docs/escalation-run.json` is what happened; `docs/escalation.svg` is how it is
drawn. Committing only the image would leave "derived from a recorded run"
unfalsifiable. Committing both means a reviewer can check the picture against
its own source without running anything.

### B2-c — the recorder refuses to write a non-escalating run

If the recorded evaluation does not come back `requires_human_review`, the
entry point exits non-zero and writes nothing. An artifact that quietly stopped
depicting an escalation would be worse than a missing one, because the README
would go on citing it.

### B3-a — the claims register is curated, not scraped

Every capability claim is a row in one delimited table. A prose scanner that
tried to find claim-shaped sentences automatically was considered and rejected:
it would have to guess what counts as a claim, and a check with false positives
gets disabled by whoever trusts it next. The limitation is stated in the README
next to the register rather than left implicit.

### B3-b — resolving and passing are two different gates

The test suite proves each named gate exists, collects, and carries no skip
marker. The `claims` CI job proves those same gates pass, by running exactly
the node ids the register names. The split is deliberate: a renamed or deleted
test stops running without turning anything red, which a green suite cannot
see, while a failing test is something CI is already good at.

`test_ci_runs_every_gate_the_register_names` asserts the CI half still exists,
so neither half can be dropped silently.

### B3-c — one parser, two consumers

`scripts/readme_claims.py` is the only reader of the register. The tests import
it and CI executes it. A second parser would drift from the first, and the
drift would silently unbind whichever claims the two disagreed about.

### B4-a — the history check falls back rather than skipping

Range resolution tries `$RST_COMMIT_RANGE`, then `main..HEAD`, then
`origin/main..HEAD`, then everything reachable from `HEAD`. A skip would let
the requirement evaporate exactly when the branch is merged — the situation it
exists to survive. CI checks out with `fetch-depth: 0` for the same reason; at
the default depth of 1 there is no history to inspect.

### B5-a — the claims register is capped, and the cap is a decision

`REGISTER_CAP = 20` in `scripts/readme_claims.py`, set to the count the
register currently holds. Existing claims were not cut; the constraint is that
the register stops growing, not that it shrinks.

The reasoning is D-1. This repository is depth evidence rather than the first
thing read, and everything added to the README competes with the escalation
artifact for the same thirty seconds of a reviewer's attention. Twenty claims
already exceeds that budget, so the next one has to be worth displacing
something.

Enforced rather than encouraged: raising the cap requires an entry in this file
in the same commit, and the entry must mention the new value. Touching the log
for an unrelated reason does not launder a raise — otherwise the requirement
decays into "remember to edit two files". Lowering the cap needs no entry;
tightening a bound needs no permission.

The check walks the commit range with the same resolver RST-B4 uses
(`tests/support/git_range.py`), so it fails closed on a history it cannot
trust rather than passing over one it cannot read.

### Out of scope, deliberately

- No adversarial case set, failure taxonomy or known-miss register. Assigned to
  the flagship demo repository under D-5.
- No change to the confidence router's thresholds or the audit chain's
  implementation.
- No hosted deployment. The artifact requirement is satisfied in-repo.
- The `same result` matcher quirk found while checking the routing table is
  recorded in docs/LESSONS.md and left unfixed; changing the matcher would move
  every recorded number here.
