# CareFlow local application · PR1

CareFlow compares two synthetic chart reconciliations, checks a fictional prior
authorization request, and saves evidence-backed draft revisions in SurrealDB.
Both clinician and mock payer interfaces display **Synthetic demo**. Comparisons
are deterministic record comparisons; there is no model call in PR1.

This project lives at the root of the `CareFlow-ai` GitHub checkout
(`C:\Users\piush\Desktop\CareFlow-ai` on this machine).
Run the following commands from the repository root. Python 3.11+ and Docker Desktop are
required. Verification used Python 3.12.14, the versions in `requirements.lock.txt`,
and SurrealDB **v2.3.10**. The lock includes the test dependencies; it does not
install the optional cloud integrations.

## Start locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
```

Set `SURREAL_PASSWORD` in your ignored `.env` to a local password. All application
and seed configuration loads from this file, with shell variables taking
precedence. No credential is provided in the example. On macOS/Linux, activate
with `source .venv/bin/activate` and copy with `cp .env.example .env`.

```powershell
docker compose up -d
python scripts/seed_surreal.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [CareFlow](http://127.0.0.1:8000). The database binds only to loopback on
port 8001. Its named volume retains charts, tasks, events, and draft revisions.
The explicit Compose project name preserves the existing local database volume
when the repository is moved or renamed.
If the seed runs before the database is ready, wait for startup and rerun it.
Seeding is repeatable: it restores only the fixed baseline IDs and metadata,
leaving workflow history intact. `docker compose down` retains the volume.

For the already configured workspace, the isolated `.venv`, ignored local `.env`,
and database volume were prepared during implementation. Keep that `.env` when
restarting; replacing its password requires matching database configuration.

## Walk through the application

1. Select Avery Demo and review baseline records. The comparison shows lisinopril
   changing from 10 mg daily to 20 mg daily and metformin unchanged. Effective
   dates and recorded times are displayed separately. Open a source ID to inspect
   its exact record, including the signed source notes.
2. Review the proposed form values and supporting sources. Open the mock payer
   draft and select **Save draft**. Reload to see the persisted revision.
3. Select Morgan Demo with **missing referral**. The chart comparison shows no
   amlodipine change; the request is blocked with `MISSING_REFERRAL`, and no draft
   can be saved. Select **corrected referral** to create a new valid task. The
   failed attempt stays in history.
4. Stop and restart Uvicorn. Open an existing task from saved history to recover
   its context, validation, draft values, revisions, and events from SurrealDB.

Pharmacy feeds are unavailable for both patients: dispensing and fills are
unknown. The checklist is invented for these fixtures; it makes no clinical,
coverage, or medical necessity decision. Submission and approval routes always
return HTTP 403 in PR1, including after a draft save.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `SURREAL_HTTP_URL` | SurrealDB HTTP endpoint | `http://127.0.0.1:8001` |
| `SURREAL_NAMESPACE` | Namespace | `careflow` |
| `SURREAL_DATABASE` | Database | `demo` |
| `SURREAL_USER` | Local database user | `root` |
| `SURREAL_PASSWORD` | Required password, never logged | Empty |
| `CAREFLOW_DEMO_MODE` | Enable task-scoped scenario selector and API | `true` |

Setting demo mode to `false` hides the selector and rejects scenario requests on
the server. Baseline synthetic patient review remains available. This local
application has no login system and is intended for loopback use with these
synthetic fixtures. The seed utility refuses remote hosts.

The application returns HTTP 503 and a visible storage failure when database
access fails; it never substitutes memory or fixture files for persistent storage.

## Structure and persistence

- `app/main.py`: application factory, API and HTML routes, failure responses.
- `app/schemas.py`: strict API inputs; unsupported fields and coerced units fail.
- `app/repository.py`: separate patient and workflow repository classes behind a
  shared facade, plus the HTTP client and test injection protocol.
- `app/context.py`: patient ownership checks and task-scoped scenario deltas.
- `app/comparison.py`: compare complete reconciliations at distinct effective
  instants; duplicate agreeing records retain their citations, conflicts block.
- `app/validation.py`: field types, dates, signatures, encounters, identity,
  provider, service matching, and the fictional completeness rules.
- `app/workflow.py`: context hashes, validation, workflow state, draft revisions.
- `app/templates/` and `app/static/`: escaped Jinja views and plain JavaScript.

Patient data is read using server-bound patient filters in a read transaction;
only providers named by that patient's request are loaded. Each `workflow_run`
stores its immutable context snapshot, scenario, patient/request/task IDs, source
IDs, context revision/hash, validation, status, draft revision/hash, and timestamps.
The revision is the canonical context's SHA-256, so corrected scenarios produce
new task contexts. Existing tasks remain bound to their captured records.

`payer_draft` and `workflow_event` records are appended with generated IDs.
Task creation and its events are atomic. Draft insertion, event insertion, and
task revision update are one transaction with an expected-revision check. A
concurrent loser receives HTTP 409 and must reload. Earlier events and draft
revisions are never rewritten. The draft hash includes task and patient identity,
context hash, values, provenance, validation, revision, and timestamp.

The browser submits exact values plus task/patient identity, context hash, and
expected revision. The server revalidates the stored snapshot and recomputes its
hash before every write; substituted identities and unsupported changes fail.
Source routes enforce the task's permitted context, including when a caller knows
another patient's record ID. Wrong-patient scenario snapshots are private audit
evidence; no contaminated records or comparison are exposed.

SurrealDB integration uses its documented
[`POST /rpc` query method with JSON variables](https://surrealdb.com/docs/reference/rest-api/http-protocol#parameters),
checked before implementation and tested against v2.3.10. Patient IDs, task IDs,
payloads, and event values use bindings. SQL table names are fixed application
code; fixtures retain the supplied SurrealQL baseline seed. No SDK or model can
construct application queries. `expectations.json` is only read by tests.

## Verification

```powershell
python scripts/check_data.py
python -m pytest -q -m "not integration"
$env:CAREFLOW_RUN_DB_TESTS='1'
python -m pytest -q -m integration
```

On macOS/Linux, enable real database tests with
`CAREFLOW_RUN_DB_TESTS=1 python -m pytest -q -m integration`.
The two integration tests create uniquely named disposable test databases,
seed them, verify real HTTP persistence and concurrency, and remove those test
databases afterward. They use the local `.env` connection and never wipe `demo`.
Without explicit enablement, they are reported as skipped, not as passing.
See `VERIFICATION.md` for the checks performed during this implementation.

The original eight standard-library reference checks remain runnable without
application dependencies. New tests cover all six scenario expectations,
patient/source isolation, doses and citations, types and relationships, invalid
saves, revisions, concurrency, escaped source text, unavailable storage,
submission rejection, and recovery through a new application/repository.
In-memory storage exists only under `tests/` and must be explicitly injected.

## Preserved starter and later phases

The dataset, six scenario bundles, expectation oracle, SurrealQL seed, model
schema/prompt, `core.py`, and optional Daytona/Phoenix scripts are preserved.
Application validation extends the reference planner; fixture records are not
regenerated or modified. Failure scenarios apply only to a task copy of baseline
database records. The complete original roadmap remains in
`IMPLEMENTATION_GUIDE.md`; its cloud orchestration and approval steps are later
phases, not PR1 behavior.

`requirements.integrations.in` remains the optional integration input. Install and
smoke-test it separately before adding Daytona, a model provider, Phoenix, Anchor,
ElevenLabs, or AWS. No paid service or deployment is invoked by the PR1 app/tests.
Do not run `scripts/generate_synthetic_data.py` unless you intend to restore the
packaged fixture files, since it overwrites those fixtures.
