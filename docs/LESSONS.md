# Lessons

What this repository learned by being worked on. Newest first.

## 2026-09-17 — reachability and evidence (RST-B1 … RST-B4)

### A README is the least-tested file in a repository, and it was wrong

The routing table under "Confidence threshold" claimed the seeded strong answer
scored coverage 1.00 at confidence 100, and that the decisively wrong one
scored 98. Running the seed gives 0.80/84 and 0.00/100. Two of three rows were
wrong, in the section documenting the feature the repository is most proud of,
in a repository whose CI was green the whole time.

Nobody wrote those numbers carelessly — they were plausible, and they were
never re-read after the scoring code moved. That is the point: prose has no
regression test, so it rots silently while the suite stays green.

The fix is not proofreading. `test_the_readme_routing_table_matches_the_seeded_run`
runs the seed and compares the database against the table. The table cannot
drift again without something going red.

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
