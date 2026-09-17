# Lessons

What this repository learned by being worked on. Newest first.

## 2026-09-17 — reachability and evidence (RST-B1 … RST-B5)

### PATTERN — false public claims survive indefinitely under green CI

Two of three rows in the README's routing table were wrong, in the section
documenting the feature this repository is proudest of, while five CI jobs ran
green over them for the life of the table. This is the exact failure the
repository argues against, committed by the repository itself.

The pattern generalises past this table: **a claim with no gate has no failure
mode.** Code that stops working goes red. Prose that stops being true does
nothing at all — it just sits there being read. CI is not evidence about any
sentence in a README unless something binds the two, which is why the claims
register exists and why it is enforced rather than encouraged.

The tell is cheap to check: for any sentence a reader would act on, ask what
would go red if it became false. If the answer is nothing, the sentence is
unverified regardless of how green the badge is.

The specifics: the table claimed the seeded strong answer scored coverage 1.00
at confidence 100, and the decisively wrong one 98. The seed gives 0.80/84 and
0.00/100. Nobody wrote those numbers carelessly — they were plausible, and they
were simply never re-read after the scoring code moved.

The fix is not proofreading. `test_the_readme_routing_table_matches_the_seeded_run`
runs the seed and compares the database against the table. The table cannot
drift again without something going red.

### A parser that skips what it cannot read is a gate checking nothing

The claims parser was fixed once for a stray `|` in a cell. That fix was
case-specific, and review caught it: three other shapes were still dropped in
silence — a row that lost its leading pipe (still valid GitHub-flavoured
markdown, and an utterly ordinary edit), a row whose identifier cell was blank,
and any stray prose left between the markers. In each case the register parsed,
the suite went green, and one fewer claim was being checked than the README
displayed.

That is the same defect as the routing table above, one level in: a mechanism
reporting success while quietly measuring less than it appears to.

The fix is structural rather than another special case. Every non-blank line
between the markers must be accounted for as exactly one of header, alignment
row, or claim, and `rows-parsed == rows-present` is asserted against the real
register. Fixing the case you were shown, when the case was an example of a
class, leaves the class.

### "Fail closed" means the gate must distinguish its own failure from yours

RST-B4 resolves a commit range, falling back through `main..HEAD`,
`origin/main..HEAD`, and finally the whole history. The fallback did fail
closed — no vacuous pass could be constructed against it — but it conflated two
different findings under one message. A shallow checkout reported "no commit
mentions RST-B1", which reads as *the work was never done*, when the truth was
*the history is not here to look at*.

A gate has two outcomes, and "I could not work out what to measure" is neither
of them. Resolution now raises rather than silently measuring something else,
and the fallback path has its own tests: a non-repository, a shallow clone, and
an override naming no commits each refuse, while a trustworthy history missing
the identifiers still reports missing work.

### "Coverage 1.00" was actually 0.80, and the reason is a matcher quirk

The strong idempotency answer misses `same result` because
`app/domain/scoring.py` keeps `.` as a word character, so the answer's
"…produces the same result." tokenises with the period attached and the
whole-phrase match fails.

Left alone deliberately. Changing the matcher would move every recorded number
in this repository, and adversarial cases and known-miss registers are assigned
elsewhere under D-5. Recorded here so the next reader does not rediscover it.

### A check with a false positive is worse than no check

The first version of `test_the_artifact_needs_no_network_to_render` asserted
`"http://" not in svg`. It failed immediately on `xmlns="http://www.w3.org/2000/svg"`
— a namespace *name*, which is never fetched. A check that fires on correct
code gets weakened or deleted by whoever meets it next.

Rewritten to strip xmlns declarations first, and paired with
`test_the_network_check_would_notice_an_external_reference`, which smuggles an
`<image href="https://…">` into a copy and asserts the guard still trips.
Mutating the *check* is a different experiment from mutating the subject.

### `-q` twice is `-qq`, and `-qq` silently changes the answer

`pytest --collect-only -q` prints node ids. The project already sets `-q` in
`addopts`, so the subprocess was really running `-qq`, which prints per-file
counts instead. Every claim binding read as "not collected" — a check failing
open in the most confusing direction available.

Fixed by clearing the project's options explicitly: `-o addopts=`. A subprocess
that depends on the caller's configuration is not a measurement.

### Parametrising over an empty list is a skip, not a failure

`@pytest.mark.parametrize("claim", CLAIMS)` with `CLAIMS == []` makes every
per-claim test disappear quietly. An emptied or malformed claims register would
therefore have removed most of RST-B3's enforcement while the suite stayed
green. `test_the_register_is_not_empty` is the check that stays behind, and
`parse_claims` raises rather than returning `[]`.

### Test-first, and the red states were real

Each RST landed as a failing test, then the code. The pre-commit full-suite run
recorded `test_a_commit_names_each_rst_identifier` failing for all four
identifiers and `test_the_work_is_not_squashed_into_one_commit` failing with
zero matching subjects — the RST-B4 red state, observed before any commit in
this branch existed.

### The slow suite was the disk, not the code

A full run appeared to take over ten minutes. The process had used four seconds
of CPU and was parked in `jbd2_log_wait_commit`: the per-test database rebuild
was contending with everything else on the host. On tmpfs the same 47 tests
finish in seconds. Worth naming because "the tests are slow" would have been
the wrong diagnosis and the wrong fix.
