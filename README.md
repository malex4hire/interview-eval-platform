# Interview Response Evaluation Platform

[![tests](https://github.com/malex4hire/interview-eval-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/malex4hire/interview-eval-platform/actions/workflows/ci.yml)

Backend + API for automated technical interviews. Candidates answer questions
(text or audio), an LLM pipeline scores them against a rubric, low-confidence
results go to a human, and everything gets written to an append-only audit log.

The interesting part isn't the CRUD. It's what happens when a model decides
something about a person: the model's verdict and the routed verdict are stored
separately, nothing is ever overwritten, and the audit log is built so tampering
is detectable even by someone with database access.

![An interview answer scoring 56 against a confidence threshold of 70, routed to requires_human_review instead of standing as the outcome](docs/escalation.svg)

That image is a recording, not a mock-up. `python -m scripts.escalation_artifact`
re-drives the API against a throwaway database and redraws it, and
`tests/test_escalation_artifact.py::test_a_fresh_regeneration_matches_the_committed_artifact`
fails if a fresh render and the committed one disagree about anything other than
a timestamp. The transcript it was drawn from is committed beside it, in
[`docs/escalation-run.json`](docs/escalation-run.json).

```bash
git clone https://github.com/malex4hire/interview-eval-platform
cd interview-eval-platform
./demo.sh
```

One command, no credential, nothing to answer. It builds the virtualenv,
creates the schema with its append-only triggers, seeds two tenants' interviews,
and serves the API with the human-review queue already populated.

## Setup

`./demo.sh` is the whole of it, and re-running it is safe — it seeds only when
the database holds no evaluations, so a recorded verdict is never overwritten.
The steps it runs, if you would rather do them by hand:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 -m scripts.init_db
python3 -m scripts.seed

uvicorn app.main:app --reload
```

Docs at http://127.0.0.1:8000/docs. Python 3.10+, SQLite by default.

```bash
pytest
```

### Seed users

Password for all of them: `demo-password-123`

| Email | Role |
|---|---|
| admin.acme@example.com | org_admin (tenant A) |
| admin.globex@example.com | org_admin (tenant B) |
| cand.jordan@example.com | candidate (A) |
| cand.priya@example.com | candidate (A) |
| cand.luis@example.com | candidate (A) |
| cand.wei@example.com | candidate (B) |

Two admins so you can check that tenant isolation actually works.

## Confidence threshold

`CONFIDENCE_THRESHOLD`, default 70. Below it, an evaluation is routed to
`requires_human_review` instead of standing on its own.

Confidence is certainty, not score. The mock provider derives it from how
decisive the rubric coverage is — 0% and 100% are both easy calls and score
high, ~50% is genuinely unclear and scores low. So a confidently wrong answer
(`incorrect` at 98) is normal and correct. If confidence just restated the
score, the review queue would fill up with obvious failures and miss the cases
where a human actually helps.

From the seed data:

| Answer | Coverage | Confidence | LLM | Routed |
|---|---|---|---|---|
| full rubric | 1.00 | 100 | correct | correct |
| nothing relevant | 0.00 | 98 | incorrect | incorrect |
| half the rubric | 0.50 | 54 | incorrect | requires_human_review |

## API

Bearer token on everything except register/login and `/health`. Anything
outside your tenant returns 404 rather than 403, so you can't confirm a
resource exists by probing for it.

### Auth
```
POST /auth/register     {email, password, role, full_name?, tenant_admin_id?}
POST /auth/login        -> {access_token, role, user_id, tenant_admin_id}
GET  /auth/me
```

### org_admin
```
POST /interviews                          {title, description?, questions[]}
GET  /interviews
GET  /interviews/{id}
POST /interviews/{id}/assignments         {candidate_id}
GET  /interviews/{id}/responses           responses + current eval + history
POST /responses/{id}/reevaluate           {reason}  -> new version
GET  /reviews/pending
POST /evaluations/{id}/review             {verdict, notes?}  409 if not flagged
GET  /interviews/{id}/audit-log
GET  /audit-log
GET  /audit-log/verify
```

### candidate
```
GET  /me/interviews
GET  /me/interviews/{id}/questions
POST /me/interviews/{id}/questions/{qid}/responses    multipart: text/audio
GET  /me/responses/{id}/evaluation
```

### Try it

```bash
BASE=http://127.0.0.1:8000
ADMIN=$(curl -s $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin.acme@example.com","password":"demo-password-123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s $BASE/reviews/pending  -H "Authorization: Bearer $ADMIN"
curl -s $BASE/audit-log/verify -H "Authorization: Bearer $ADMIN"
```

## Data model

```
users ──┬─< interviews ──< questions ──< responses ──< evaluations ──< human_reviews
        │      owner_admin_id = tenant                     │
        ├─< interview_assignments                          └──< llm_calls
        └─< audit_log
```

No organisations table. The org_admin is the tenant: `interviews.owner_admin_id`
is the scoping key, it rides in the JWT, and every query filters on it in
`repositories.py` so route handlers can't skip it.

Evaluations are versioned. Re-evaluating appends v2 and flips `is_current`;
v1 keeps its verdict, confidence and raw output. Otherwise "why did this
candidate's result change" has no answer.

Two verdict columns: `llm_verdict` is what the model said, `verdict` is the
state after the confidence gate. Merging them would throw away the model's
actual output every time routing kicked in. A human review is a third row
rather than an edit, so all three positions survive. `final_verdict` composes
them.

`llm_calls` is separate from `evaluations` — cost and latency are operational,
the verdict is a domain record, and a failed call that produced no evaluation
still needs to land somewhere.

## Audit log

```
entry_hash = SHA256("v1|prev_hash|seq|event_type|subject_type|subject_id|actor_id|canonical_json(payload)|created_at")
```

Sequence is per-tenant, gap-free, starts at 1 with `prev_hash` = 64 zeros.
Payload carries the prompt, raw output, matched/missing concepts, confidence,
the threshold at the time, and both verdicts. Written in the same transaction
as the evaluation it describes.

Append-only is enforced in the database: triggers on SQLite, revoked grants on
Postgres. Application-level discipline isn't enough — anything reachable
through the ORM can be bypassed.

`verify_chain()` reports three different failures, because they mean different
things: a sequence gap means a row was removed, a `prev_hash` mismatch means
history was re-linked, an `entry_hash` mismatch means a row was edited in place.

The tests drop the triggers first, to stand in for someone with direct database
access, then check that verification fails on an edited payload, a deleted
entry, and a rewritten hash. The rewritten-hash case is the interesting one:
recomputing the digest still breaks the next entry's `prev_hash`.

Interview-scoped exports carry the tenant-wide verification result. A filtered
slice has legitimate sequence gaps, so verifying the slice on its own would
report tampering that didn't happen.

## LLM abstraction

```python
class EvaluationProvider(Protocol):
    name: str
    model: str
    def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...

class Transcriber(Protocol):
    name: str
    def transcribe(self, audio_path: str) -> TranscriptionResult: ...
```

Switching providers is `LLM_PROVIDER=...`, not a code change. Registration
points for real providers are marked in `providers/registry.py`.

The provider returns a verdict, explanation, confidence and raw output — that's
it. Thresholding and routing live in `domain/verdicts.py`, otherwise every new
provider could quietly redefine when a human gets pulled in.

The mock is deterministic. Same input, same evaluation, every time — a random
mock makes tests flaky and audit replays unreproducible.

`MockTranscriber` reads the uploaded file as text when it decodes as UTF-8,
which is how the fixtures carry audio answers without an STT dependency.
Binary uploads get a deterministic placeholder so the path still works.

## Decisions

| # | Decision |
|---|---|
| D-1 | org_admin is the tenant, no organisations table |
| D-2 | assignment table gates candidate visibility |
| D-3 | global confidence threshold via env |
| D-4 | evaluation runs synchronously on submit |
| D-5 | evaluations versioned, never overwritten |
| D-6 | audit scope: evaluations + human reviews |
| D-7 | only flagged evaluations can be overridden |
| D-8 | audit export is a full JSON array + verification envelope |

Smaller calls worth knowing about:

- Passwords use stdlib PBKDF2-HMAC-SHA256 rather than passlib/bcrypt. One less
  compiled dependency. Work factor is `PBKDF2_ITERATIONS` (480k default); the
  test suite drops it to 1000, which takes a full run from ~7 minutes to
  seconds. The count is stored in each hash, so changing it doesn't invalidate
  anything.
- PyJWT instead of python-jose.
- Login hashes against a dummy value when the user doesn't exist, so timing
  doesn't leak which emails are registered.
- Uploaded filenames are never used as paths. UUID name, allowlisted suffix.
  Otherwise the upload endpoint is an arbitrary file write.
- Candidates don't see `expected_concepts` or `missing_concepts`. The rubric
  shouldn't be reconstructable from feedback.
- One response per question per candidate. A 409 is better than silently
  overwriting something already scored and audited.

## Layout

```
app/
  domain/          hashing, scoring, verdicts — stdlib only
  providers/       protocols + mock implementations
  services/        evaluation pipeline, audit, review, storage
  repositories.py  tenant-scoped queries
  models.py        schema
  schemas.py       pydantic request/response
  api/             deps + routers
scripts/           init_db, seed
tests/
```

`app/domain/` has no third-party imports. The hash chain and verdict routing
are the parts that have to be right, and they're testable without a database
or a framework.

## Not done

Scoped out per the brief, with where each would attach:

- Async evaluation. `EvaluationService.evaluate_response()` is plain Python
  taking a session, so moving it behind a queue changes the caller — submit
  would return 202 with a pending status.
- Real providers. Hooks in `providers/registry.py`. Each needs a timeout,
  bounded retry and a breaker; `LLMCall` already records failures.
- Alembic. `scripts/init_db.py` covers it for now; the append-only statements
  should move into the first migration.
- S3. `services/storage.py` is the only thing touching the filesystem.
- Rate limiting, refresh tokens, token revocation.
- Per-interview thresholds, separate reviewer role, scheduled chain
  verification with alerting.
