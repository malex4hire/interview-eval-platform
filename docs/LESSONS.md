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

### REFINEMENT — a gate has two outcomes, but a failure has two causes

A gate has two outcomes, verified or fail. That is unchanged. **But a failure
has two causes, and they have to be reported differently: the property was
violated, or the evidence was unavailable.**

RST-B4 resolves a commit range, falling back through `main..HEAD`,
`origin/main..HEAD`, and finally the whole history. Review expected a vacuous
pass in the fallback. There wasn't one: a repo with no matching commits, a
shallow clone with no branch range, `HEAD..HEAD`, a bogus range and a
non-repository were all tried, and every one failed closed.

**That enumeration was also the mistake, and a later review caught it.** Every
scenario listed above probes the FALLBACK path. The branch-range path was never
probed, and that is where the hole was: the shallow guard sat *after* both
branch-range attempts, so `git clone --no-single-branch --depth 2` — which
leaves `origin/main` resolvable — produced `origin/main..HEAD`, returned the two
commits it could see, and reported the three older ones as missing work. The
commonest shallow shape there is, straight through the guard.

Two lessons, and the second is the sharper one. Moving the check ahead of every
derivation was the fix. But **"I tried five scenarios and all of them failed"
is a claim about coverage, and five negative results say nothing about the path
none of them touched.** Name the paths, not the attempts — this file asserted
the fallback was exhaustively probed while a second, unprobed path carried the
defect.

**The original defect was that it failed for the right reason and said the
wrong one.**
A shallow checkout reported *"no commit mentions RST-B1"*, which is a false
accusation: the work was there, the history was not. And that erodes trust as
fast as a vacuous pass, because of what the next person does with it — either
they go chasing work that was already done, or they learn to read real failures
as tooling artifacts and start waving them through. A gate nobody believes has
stopped being a gate.

So: **report the cause, fail either way.** Resolution now raises
`RangeUnresolvable` for a non-repository, any shallow repository (checked before
a range is derived, not after), or an override naming no commits, and each
refusal carries its own test — including the `--no-single-branch --depth 2`
shape that got through the first time.
The load-bearing one is the opposite direction — a *trustworthy* history that
genuinely lacks the work must still report missing work — because without it
the "evidence unavailable" branch would quietly swallow the very failure the
gate exists to report.

### Three of this session's own "structural fixes" were decoration or blind

An automated review read the branch and returned six substantiated findings,
every one reproduced by execution. They are worth recording as a set, because
the pattern across them is sharper than any one of them:

| what it was presented as | what it was |
|---|---|
| the structural fix closing the parser fail-open | an arithmetic identity that could not fail — deleting it turned nothing red |
| "the entry must mention the new value" | a bare substring search that any dated heading satisfies |
| the cap detector | disarmed entirely by writing `REGISTER_CAP: int = 21` |
| the shallow-clone refusal | guarded only the fallback path; the commonest shallow shape walked past it |

**Every one was written in the same session as a lesson warning against exactly
that failure**, and shipped under a green suite and five green CI jobs. The
tautological accounting check landed in the same batch as the paragraph saying
a check that cannot fail is not a check. That is not carelessness about the
doctrine — it is evidence that knowing the doctrine does not protect you, and
that the only thing which actually catches this is an adversary who executes
the code rather than reading it.

The mechanical takeaways, each earned here:

- **Mutate the fix, then mutate the shape an editor produces.** The cap
  detector survived every mutation aimed at the value it read, and died to a
  type annotation nobody thought to try.
- **A regex over source text is a parser, and parsers have a third state.**
  Present, absent, and *unreadable* — and collapsing unreadable into absent is
  what disarmed the gate. The same distinction the range resolver needed, one
  layer down, missed while writing the range resolver.
- **A check that is a property of history needs a forward remedy.** The cap
  gate would have gone permanently red on its first violation, with no exit
  except a history rewrite in a repository whose rule is never to rewrite.
  Naming the offending SHA in the decision log is the remedy.

### A gate scoped to "this branch" for a requirement about HISTORY blocks every later branch

RST-B4 asserts the work landed as one commit per requirement identifier. The
gate resolved `main..HEAD` and demanded the identifiers appear in THAT range —
which is true of the branch that did the work, and false of every branch after
it.

It was caught by the first pull request that followed: a docs-only change, which
failed with `no commit mentions RST-B2 in origin/main..HEAD`. Not a flake and
not a wrong check name — the gate was working exactly as written, against the
wrong noun. Left alone it would have blocked every future merge, and because the
`pytest` jobs are required, that means every future merge full stop.

**The tell is the tense.** "The work landed as incremental commits" is a claim
about what is IN the history. Once it has landed, it is a property of the
repository, not of whatever range happens to be checked out — and a later branch
does not re-land it. Resolution now reads the full history, with the explicit
override and the shallow refusal intact, and carries both rows: a later branch
carrying no RST work passes, and a history genuinely missing the work still
fails.

**What actually found it was using the thing.** Two adversarial reviews, twenty
findings, and a mutation set that never tried the one experiment that mattered —
*run this gate from a branch that is not the branch it was written on.* The
first real merge attempt found it in under a minute. Some defects are only
reachable by the system being used the way it will actually be used, which is an
argument for shipping a guard early enough that it gets exercised, not for
reviewing harder.

### A check that only ever goes red is indistinguishable from a broken one

Mutation testing naturally produces red rows: break the thing, watch the gate
fire. All of those together still do not show the gate can tell the difference
between a violation and anything at all. **A check hardwired to fail passes
every red test in the set.**

The truth table needs the row where the legitimate path goes green. RST-B5's
cap gate has four:

| | |
|---|---|
| unmutated | green |
| a 21st claim added, cap untouched | red |
| cap raised with no decision entry | red |
| cap raised **with** an entry naming the value | **green** |

The last row is the one doing the work. Without it, "raising the cap with a
decision entry is allowed" is an untested claim about the gate, and a check
that refused every raise — including legitimate ones — would look identical
from the outside until someone hit it for real.

The same reasoning applies one level up, to the mutation harness itself. Three
harness bugs in this session reported results that were not measurements: a
case-sensitive grep for `failed` that missed pytest's `FAILED` and reported
seven gates green; `git archive` producing a tree with no `.git`, so three
mutations "failed" in a fixture rather than on the mutation; and two paths
passed as one shell argument, which turned the control run red. **A control row
catches all three, which is why one belongs in every mutation set.**

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
