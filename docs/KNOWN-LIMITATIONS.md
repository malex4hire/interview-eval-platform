# Known limitations of the checking apparatus

These are defects in the **gates**, not in the system they guard. Each was found
by adversarial review, reproduced by mutation, and is recorded here rather than
fixed — fixing a gate that guards a gate is an unbounded regress, and the
honest alternative to that regress is writing down exactly where the floor is.

Every entry names the mutation that demonstrates it. None of them is a claim
about the interview platform itself; they are claims about how far these tests
can be trusted.

Recorded 2026-09-17, against `feat/reachability-and-evidence`.

---

## L-1 — widening the volatile allowlist silently disarms the regeneration gate

`tests/test_escalation_artifact.py` compares a fresh render of
`docs/escalation.svg` against the committed one, normalising a volatile-field
allowlist first. **Measured: all five allowlist patterns match zero characters
of the committed artifact today**, so `normalise_volatile` is currently the
identity function and the comparison is exact.

That also makes `test_the_normaliser_does_not_hide_a_real_difference` a
tautology: it reduces to `tampered != original`, which the line above it already
asserts.

**Reproduction.** Tamper the committed confidence (`>56 <` → `>99 <`): the
regeneration test goes red, so the gate works today. Apply the same tamper *and*
append one rule, `(re.compile(r"\d+"), "<n>")`, to `_VOLATILE`: all nine tests
pass and a stale artifact with the wrong confidence ships.

**What it means in practice.** The allowlist is load-bearing and unguarded.
Anyone widening it is disabling the comparison, and nothing will say so.

## L-2 — the CI-wiring check is a substring match

`test_ci_runs_every_gate_the_register_names` asserts that the literal
`scripts.readme_claims` appears somewhere in `ci.yml` and that a job named
`claims` exists. It does not check that the job runs anything.

**Reproduction.** Replace both steps of the `claims` job with
`- name: Placeholder / run: echo "gates not run"`, leaving a comment mentioning
`scripts.readme_claims` above it. The test passes. The passing half of the
claims register is fully disabled and nothing goes red.

## L-3 — the squash check counts subjects without checking which identifier

`test_the_work_is_not_squashed_into_one_commit` counts commit subjects naming
*any* RST identifier and compares that count to the number of identifiers. Its
docstring claims one commit per identifier; that is not what is asserted.

**Reproduction.** Five commits whose subjects all read `RST-B1: slice N`, with
the other four identifiers present only in the commit bodies. All thirteen
commit-history tests pass. Four of five requirements are named by zero subjects.

## L-4 — the network check's own mutation check does not call the network check

`test_the_network_check_would_notice_an_external_reference` re-implements the
xmlns stripper inline and asserts two strings survive it. It never invokes the
forbidden-token list it is named for.

**Reproduction.** Delete `"http://"`, `"https://"`, `"//"`, `"xlink:href"` and
`"<image"` from that list: all nine tests pass. With those removed *and* a real
`<image href="https://example.invalid/logo.png"/>` smuggled into the committed
SVG, the mutation check still passes — only the byte comparison notices, and for
an unrelated reason.

## L-5 — "above the fold" is satisfied by any textual mention

`test_the_readme_shows_it_above_the_first_section_heading` uses `in` and
`.index()` on the bare path `docs/escalation.svg`. A code span, a plain link or
an HTML comment all satisfy it; RST-B2 requires the image to be *rendered*.

**Reproduction.** Replace the `![alt](docs/escalation.svg)` line with
`` See the escalation diagram: `docs/escalation.svg` (not shown). `` — 138 tests
pass. The README's headline image is gone and nothing notices.

## L-6 — the no-prompt check does not look for prompts

`test_the_command_asked_the_operator_for_nothing` asserts only that the captured
output contains neither `EOFError` nor `Traceback`. Closed stdin enforces
non-blocking, not non-asking.

**Reproduction.** Add `printf "Proceed? [y/N] "` and `read -r _answer || true`
before the seed step in `demo.sh`: all bring-up tests pass, while
`Proceed? [y/N]` appears verbatim in the very log file the test reads.

A bare `read -r x` under `set -euo pipefail` *does* abort on EOF and is caught
by the fixture — so the hole is specifically the tolerant forms (`|| true`,
`-t`, a default value), which is what a script written to stay CI-friendly uses.

## L-7 — the cap exemption hatch is broader than its purpose

`_exempted_shas` harvests any 7–40 character hex string on a line containing
`REGISTER_CAP` in `docs/DECISIONS.md` at HEAD, and the exemption is applied
before any state check.

**Reproduction.** A decision line that *rejects* the raise — "REGISTER_CAP raise
in 00d7f57437 was REJECTED; do not do this" — clears the offender. Naming a sha
on a `REGISTER_CAP` line also clears a cap *disarm*, which the detector's own
comment labels the offence it most wants to catch.

Related and now fixed rather than recorded: the prose example documenting this
mechanism used a full hex sha, which the same reader harvested as a live
exemption. The example is a non-hex placeholder now, and
`_exempted_shas()` returns empty against the real repository.

## L-8 — the live cap assertion proves nothing on its own

`test_no_commit_raises_the_cap_without_a_decision_entry` runs against the real
commit range, where **no commit has the shape (parent readable, child readable,
value increased)** — measured states across the branch are `absent ×4 →
unreadable ×7 → ok(20) ×4`. The decision-log branch is never reached, so
`offenders == []` by construction.

The mechanism itself is genuinely covered, by paired red/green fixtures on
throwaway repositories. It is the repository-level invocation that is vacuous.

## L-9 — smaller items, recorded without individual reproductions

- `test_the_register_is_within_its_cap` (`<=`) cannot fail unless
  `test_the_cap_matches_the_register_it_bounds` (`==`) also fails. No
  independent failure mode.
- The `<uuid>` volatile rule is unreachable: the `<digest>` rule runs first and
  rewrites a uuid's hex segments.
- `_mentions_the_raise` uses a substring, so a line reading
  `REGISTER_CAP raised to 121` satisfies a raise to 21.
- The determinism field list in `tests/test_documented_behaviour.py` is
  hand-maintained; a field added to `EvaluationResult` later is silently
  unchecked. `dataclasses.fields()` minus `latency_ms` is the mechanical form.
- The skip-marker check resolves a gate by node id; a class-based or
  parametrised gate would not be matched by name. Dormant — no bound gate has
  that shape today.

## L-10 — the bare-runner claim is not guarded at merge time

Branch protection on `main` requires three checks: `pytest (3.10)`,
`pytest (3.12)` and `claims`. **`demo` is deliberately not required**, and it is
the only proof of RST-B1's claim that one command works on a bare runner — it is
the sole job that runs the bring-up test with `IEP_DEMO_BARE=1`, where
`demo.sh` must find an interpreter, build a virtualenv and install requirements
itself.

So the **dependency-install leg is unguarded at merge time**. A pull request can
merge with `demo` red.

What remains covered: the bring-up *behaviour* — clean tree, no manual step, no
credential, a seeded evaluation carrying a routing disposition, an escalation in
the queue — is asserted by the same test running in shortcut mode inside all
three `pytest` jobs, which are required. Only the install path is exposed.

**Why it is unrequired rather than required.** `demo` performs two real PyPI
installs (runner-level, then inside the virtualenv it builds), which makes it
the job most likely to fail for reasons unrelated to the code. A check that goes
red on a PyPI outage becomes a check someone removes, and its true positives
leave with it. It still runs on every pull request and its failures are visible;
it just does not block.

**The caveat on that reasoning, stated because it is not a measurement.** The
recommendation came from dependency shape, not from flakiness data. The
observed record is four runs with zero failures across all five checks — which
is entirely consistent with a 20% flake rate. Four clean runs is not evidence a
job is reliable, and nothing here should be read as claiming it is.

---

## What survived

Reported because a surviving gate is a result too. Each of these was mutated and
went red:

- `demo.sh` with the seed step removed → the bring-up probe fails, naming the
  missing demo data.
- the seed rewritten so nothing escalates → the escalation test fails.
- one confidence digit changed in the README routing table → the routing-table
  comparison fails.
- the routing table reformatted as `84%` → two tests fail.
- `*.db` dropped from `.gitignore` with a stray database present → the
  clean-tree test fails.
- a bound gate renamed → the binding test fails, naming the claim.
- the claims register emptied → four tests fail, including the one that exists
  because an empty parametrisation reads as a skip.
- `tests/support/git_range.py` — no evasion found. The shallow refusals are
  paired with inverse rows, git failures return empty and fail the gates closed,
  and the shallow guard sits before both branch-range attempts.

## The shape to expect more of

Four of the findings above are one defect wearing different clothes: **a test
named after a mechanism that does not exercise that mechanism.** L-1 and L-4 are
mutation checks that never call the thing they guard; L-2 greps for a string
instead of a behaviour; the skip check (since fixed) turned a failed regex into
silence.

The decisive experiment is always the same, and it is cheap: break the named
mechanism and see whether the test stays green.
