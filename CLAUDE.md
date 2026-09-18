# CLAUDE.md - interview-eval-platform

Repository-local working order. The estate-wide standing orders in
`~/.claude/CLAUDE.md` bind in full; this adds only what is local.

## Commits

One commit per complete update. A commit is a coherent change that stands on
its own, not a step inside one. Its message names every constraint or
requirement the change satisfies.

Do not split a change so each commit names a single identifier. Do not commit
intermediate states or fix-ups of your own uncommitted work; amend instead.

Each commit triggers a review cycle. Fewer, coherent commits cost less and
review better: a review scoped to a fragment is worse than one scoped to the
whole change.

The ordered build log carries sequence and rationale. Commit count is not
evidence of anything.

Squash and rebase remain forbidden on merge. This governs how many commits are
created, not whether history is rewritten.

The one exception to amending: where a commit's content embeds its own hash,
use a two-commit pattern (content commit, then annotation resolution). An
amended hash is a broken reference.

Commit granularity is governed by D-24 in
`abyss-independent-authority/docs/PortfolioDecisions.md`, which supersedes
the "one commit per constraint" rule this repository's commit-history gate
used to enforce.
