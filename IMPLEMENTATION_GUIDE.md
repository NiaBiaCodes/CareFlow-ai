# CareFlow hospital EHR implementation guide

Build the first version around one complete workflow: a clinician selects one of two synthetic patients, asks what changed in the charted medications, and prepares a fictional prior authorization request. The application checks the evidence, fills a mock payer form, and waits for clinician approval. Patient records and workflow history remain in SurrealDB after each Daytona execution ends.

This guide implements the task-scoped approach in the supplied Hospital EHR Daytona project brief dated 16 October 2026. Prior authorization is a proposed first workflow; the broader hospital concept remains intact. All medical details and payer requirements below are invented for software testing.

## 1 Set the first acceptance target

Use Avery Demo as the successful case and Morgan Demo as the missing-referral demonstration. A successful first build must visibly do the following:

1. Retrieve only the selected patient's records.
2. Run the patient task inside a newly created Daytona sandbox.
3. Answer one question with clickable source IDs and record dates.
4. Detect a missing referral and stop before filling or submitting a request.
5. Restore the synthetic referral and create a draft through Anchor Browser.
6. Show the draft and evidence to the clinician before simulated submission.
7. Record the result and trace, end the browser session, and delete the sandbox.
8. Start a new sandbox and recover the previous workflow result from SurrealDB.

Keep the first UI to one patient list, one question panel, one evidence drawer and one request review panel. Add voice, live pharmacy feeds, real insurance policies, full EHR features and optional AWS hosting after this path works. The supplied records use a compact custom schema; FHIR conversion can follow later.

Prior authorization requests approval before a planned service. Keep payment claims after a service as a separate future workflow.

## 2 Use a small Python stack

Use Python 3.11 or newer, FastAPI and Jinja templates with plain HTML for the first application. This keeps the application and worker in one language. A separate frontend framework is optional later.

| Component | Responsibility | Where it runs |
| --- | --- | --- |
| FastAPI application | Clinician UI, patient selection, task state, approval and result storage | Your development machine or demo server |
| SurrealDB | Authoritative synthetic records and durable workflow memory | Outside patient sandboxes |
| Daytona worker | One selected patient task and the actual model call | A new sandbox per execution |
| Anchor Browser | Navigate and fill your mock payer portal | Anchor cloud browser controlled from the worker |
| Arize Phoenix | Capture execution traces and application evaluation results | Local collector or reachable hosted collector |
| Mock payer portal | Save drafts and issue simulated receipts after approval | A route in your FastAPI application |

Keep the root database credentials in the application. Pass a narrowly selected patient bundle into the worker. The worker controls a remote Anchor browser; the browser itself runs on Anchor infrastructure. Daytona supports programmatic runtime creation and deletion, and Anchor documents cloud sessions with browser-control connections. [R1, R4]

## 3 Unpack the starter and run the offline checks

Extract the ZIP, open a terminal inside `careflow_starter`, and run:

```bash
python scripts/check_data.py
python core.py --case happy
python core.py --case missing_referral
```

On macOS or Linux, use `python3` if that is your Python command. On Windows, `py -3.11` can be used when Python 3.11 is installed through the launcher.

The first command should pass eight tests. The successful case returns `ready_for_review`, one medication change, source IDs for every form field and `submitted: false`. The missing-referral case returns `blocked` and an empty form payload. These commands use only the Python standard library.

The included `core.py` is deterministic reference logic. It provides testable facts and validation behavior for your implementation; it is not an LLM agent. The EHR application and cloud orchestration still need to be built in the following steps.

Create a virtual environment before installing the optional application and integration packages:

```bash
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in PowerShell, or `source .venv/bin/activate` in macOS, Linux or WSL. Then run:

```bash
python -m pip install -r requirements.integrations.in
```

Copy `.env.example` to `.env`. Add Daytona, Anchor and your chosen model provider key. Set `LLM_MODEL` to a model available in that account. A local Phoenix collector needs no Phoenix key. The optional scripts load `.env`; the application and worker entry points you create must also load or receive their configuration explicitly.

SDKs change. The dependency file intentionally lists packages rather than inventing a tested version lock. Once your provider smoke tests pass, run `python -m pip freeze > requirements.lock.txt` and use that lock when preparing your worker snapshot. Keep `.env` out of git and out of the clinician UI.

## 4 Understand the synthetic records

The fixed dataset contains two patients, four encounters, six signed documents, four medication reconciliations, two unavailable-pharmacy status records, two administrative requests and one fictional provider. The snapshot time is 16 October 2026 at 16:00 UTC, or noon in New York.

| Patient | Previous encounter | Latest encounter | Expected medication answer |
| --- | --- | --- | --- |
| SYN-P001 Avery Demo | 30 September 2026 | 14 October 2026 | Chart lists lisinopril changing from 10 mg daily to 20 mg daily; metformin is unchanged |
| SYN-P002 Morgan Demo | 30 September 2026 | 14 October 2026 | No change between the two charted amlodipine reconciliations |

Use the question: `What changed in this patient's charted medications between the previous and latest encounters?`

For Avery, a supported answer is: `The 14 October chart lists lisinopril 20 mg daily, compared with 10 mg daily on 30 September [MED-001-1, MED-001-2]. Metformin remains 500 mg twice daily. Pharmacy dispensing information is unavailable.` The medication snapshot records link back to the signed notes through `source_record_id`. This is a comparison of fictional chart entries, not a recommendation to change a dose.

Every patient-specific record has `patient_id`. Source records have stable `record_id` values. `effective_at` describes when the chart fact applies; `recorded_at` describes when it entered this dataset. Compare effective dates and identify conflicting records explicitly. The planned service date is in the future by design; a future clinical source record is a separate validation failure.

The mock request is for `DEMO-PT-EVAL`, six fictional units, planned for 26 October 2026. The diagnosis label `DEMO-MSK-001`, member IDs and provider IDs are deliberately fake. Do not submit them to a real service.

## 5 Keep six repeatable scenarios

Use the complete bundle files under `data/cases` to reproduce failures. `data/expectations.json` is the independent test oracle; it must stay outside the model's context.

| Case | Deliberate condition | Expected result |
| --- | --- | --- |
| happy | Avery has all required records | Ready for clinician review |
| missing_referral | Morgan's referral is absent | Block with MISSING_REFERRAL |
| corrected_referral | Morgan's original synthetic referral is restored | Ready for clinician review |
| wrong_patient | Avery's context contains a Morgan note | Block with PATIENT_MISMATCH |
| conflicting_medication | Avery has different doses at the same effective time | Block for conflicting medication records |
| missing_member_id | Avery's member ID is null | Block with MISSING_MEMBER_ID |

Pharmacy status is unavailable in every case. The agent must say that fills are unknown. Lack of a feed does not mean no dispensing occurred. This gap does not block the fictional administrative checklist because that checklist does not require pharmacy evidence.

Run `python scripts/generate_synthetic_data.py` only when you want to restore the packaged fixtures. It deterministically rewrites those fixture files, including any local edits to them. Seed only the baseline dataset into the database. Apply negative scenarios in memory or as explicitly named demo revisions so they do not contaminate every later run.

## 6 Start SurrealDB and seed the records

For a local synthetic demo, start Docker Desktop and run this command on one line:

```bash
docker run -d --name careflow-db --user 0 -p 127.0.0.1:8001:8000 -v careflow-surreal:/data surrealdb/surrealdb:latest start --user root --pass demo-local-only rocksdb:/data/careflow.db
```

Port 8001 is reserved for the database; port 8000 will be used by FastAPI. This local command runs the container as root to make the named demo volume writable and binds the database only to loopback. Replace the image's `latest` tag with your tested tag or digest after setup. The database volume preserves data across container restarts. SurrealDB documents its Docker image, port mapping and persistent storage paths. [R2]

Load the supplied records:

```bash
python scripts/seed_surreal.py
```

Expect two patient rows. The loader uses the documented HTTP SQL endpoint and checks every statement result. It updates only the fixed fixture record IDs. It does not erase your workflow tables. The local loader refuses remote database addresses; create a scoped account and a separate remote configuration when moving beyond local development. [R3]

The seed creates `patient`, `encounter`, `document`, `medication_snapshot`, `pharmacy_status`, `admin_request`, `provider` and `mock_policy` records. Database record keys use underscores; original citation IDs remain in `record_id`. Timestamps are stored as ISO UTC strings for this portable seed. Parse them before time comparisons, as the reference code does, or migrate them to typed database datetime fields.

Add `workflow_run` and append-only `workflow_event` tables in your application. Store `task_id`, `patient_id`, `request_id`, context revision and hash, status, source IDs, draft revision and hash, clinician approval, sandbox ID, browser session ID, trace ID, cleanup status and outcome timestamps. Treat saved AI answers as derived workflow memory. Retrieve the original source records again when preparing a new clinical answer.

Build a repository function `load_patient_context(patient_id, task_type)`. The server verifies the selected patient, fetches records with a bound patient filter, and includes only the matching provider. Do not let the model construct arbitrary database queries. Full records and workflow history survive outside the sandbox; each execution gets a fresh copy of only the necessary context.

This MVP uses SurrealDB records for persistent memory. It does not implement a separate SurrealDB Agent Memory SDK. If your event requires that specific product integration, add its adapter after checking its event requirements.

## 7 Build the mock EHR and payer routes

Create `app/main.py`, `app/repository.py`, `app/workflow.py`, `app/templates/clinician.html`, `app/templates/payer.html` and `worker/run_task.py`. Keep cloud adapters in separate modules so fixture checks can run without provider calls.

Use the following application contract. These routes are proposed implementation work and are not present in the starter pack.

| Route | Expected behavior |
| --- | --- |
| GET / | Show the two patients and task controls |
| GET /api/patients | Return the demo patient list |
| POST /api/tasks | Bind selected patient, question and task type to a server-generated task ID |
| GET /api/tasks/{id} | Return status, answer, source IDs and draft summary |
| GET /api/tasks/{id}/sources/{record_id} | Return a source only if it belongs to this task's allowed context |
| POST /api/tasks/{id}/approval | Record clinician approval of an exact draft revision and hash |
| GET /mock-payer/requests/{id} | Show a form bound to the task and patient |
| POST /mock-payer/requests/{id}/draft | Save a draft after checking patient and task identity |
| POST /mock-payer/requests/{id}/submit | Verify approval and issue a fictional receipt |

Run the application, once implemented, with `python -m uvicorn app.main:app --reload --port 8000`. A long task should return its ID promptly, then update status while the UI polls. Record state before starting the worker so a failed request can be recovered.

Use a persistent `SYNTHETIC DEMO` label in both interfaces. In the clinician UI show patient name and ID, encounter dates, source links, answer limitations, validation errors, the current draft and an approval button. Render source text safely as text rather than executable HTML.

In the payer template use an HTML input for each key in the reference `form_fields` object, with the exact same `name` attribute. Render patient and task identity as server-bound values. Add `data-testid="save-draft"`, `data-testid="draft-status"` and `data-testid="mock-receipt"` attributes. Saving a draft is separate from submitting a request. The server must reject submission while approval is absent, even if someone clicks a hidden button or calls the route directly.

## 8 Prove the Daytona execution path

After adding your Daytona key to `.env`, run:

```bash
python scripts/daytona_smoke.py --case happy
python scripts/daytona_smoke.py --case missing_referral
```

The script creates a sandbox, uploads the reference code and one selected context file, executes the planner inside Daytona, downloads the result and waits for sandbox deletion. These runs use your provider account and may incur usage. They do not call an LLM or Anchor. Daytona's SDK documents file transfers, execution results and deletion confirmation. [R1, R5, R6]

Replace the smoke worker with `worker/run_task.py` when the application exists. The application should create a task, build the selected context, record its hash, create the sandbox and immediately persist the sandbox ID. Upload the worker and that context. Run the task with a timeout. Validate downloaded output again on the server before saving or showing it.

Use a tested snapshot with worker dependencies already installed for repeated demos. Execute fixed application code. A language model does not need unrestricted shell access to compare records or fill this form. Pass only the model or browser credentials the task needs, and keep database administrator credentials outside the sandbox.

Use a `finally` cleanup path for each execution and persist cleanup failures for retry by sandbox ID. Idle auto-stop is a fallback; it is not proof that a running or stuck task has ended. Save results and flush traces before deleting the sandbox. Record deletion as pending until the provider confirms completion.

## 9 Add the source backed model answer

Implement a small model adapter in the worker. Give it the question, selected patient ID, relevant dated records and `agent_system_prompt.txt`. Require output matching `answer.schema.json`. The model prompt and schema are included; the provider call remains implementation work.

For the first question, derive the medication comparison from complete structured snapshots and use the model to explain that comparison. This keeps names, doses and dates checkable. Give the model the original supporting notes too, so contradictions can be surfaced rather than hidden. Defer general semantic search until there are enough records to need it.

Validate the returned schema, exact patient identity, source existence, source ownership and cited fact values. A valid source ID establishes provenance, but does not prove the claim is supported by that source. For these fixtures, compare the model's claims with the fixed medication facts and review any free-text assertions.

Limit one task to the requested question. If records are missing, answer `insufficient_records`. If a source belongs to another patient or simultaneous medication records conflict, return `blocked`. Do not infer a diagnosis or make a coverage decision. The administrative payload should continue to come from validated records and the clinician's request, rather than being invented by the model.

Acceptance check: Avery's answer cites `MED-001-1` and `MED-001-2` for the dose comparison, gives both encounter dates and acknowledges the unavailable pharmacy feed. Morgan's answer reports no difference between the two supplied chart snapshots. Run the wrong-patient and conflict scenarios through the actual model path before trusting the UI output.

## 10 Apply administrative validation before browser work

For this demo, a complete request requires a patient ID, date of birth, member ID, matching provider, requested service, units, planned service date, fictional diagnosis label, a signed final note for the requested encounter and a signed referral for that service. These are invented completeness rules. They do not represent Medicaid policy, payer eligibility or medical necessity.

Use `plan_request` and its fixed scenarios as the initial reference. Add strict request schemas and field type checks in the API. Require positive units, valid dates, unique IDs and exact request-document relationships. Validate only records at or before the context snapshot, while allowing the future planned service date.

Build `form_fields` and a parallel `field_sources` mapping. Patient details come from `PAT-001`; provider identity comes from `PROV-DEMO-001`; request values come from `REQ-001`; note and referral fields point to their document IDs. Store this evidence alongside the draft.

If validation fails, persist `blocked`, display the specific missing or conflicting record and stop. Do not start a browser session. In the missing-referral demonstration, show which document is required and let the demo operator restore `REF-002`. Create a new context revision and task attempt for the retry. Keep the original failed attempt in the history.

## 11 Connect Anchor to your mock payer portal

First make the mock payer page reachable from Anchor. `http://localhost:8000` inside a cloud browser refers to that browser's machine, not your development laptop. Host just the synthetic mock portal at an HTTPS address or expose it through a development tunnel. Set `MOCK_PAYER_BASE_URL` to that address. Keep the database, secrets and administrative endpoints private. If a URL cannot be reached, fix connectivity before debugging selectors.

Create an Anchor session inside the Daytona worker. The current Python quickstart uses `Anchorbrowser`, `sessions.create_session()` and `browser.connect(session_id)`. A session also returns a live-view URL. Keep that URL in task metadata; do not print credential-bearing browser connection URLs into the UI or logs. [R4]

Implement the browser adapter in this order:

1. Confirm that application validation passed and the destination host matches your configured mock portal.
2. Create a bounded browser session and save its ID immediately.
3. Navigate to `/mock-payer/requests/{task_id}` and verify the displayed task and patient IDs.
4. Fill the named fields from the validated payload.
5. Read each input value back and compare it with the expected value.
6. Click only `save-draft`, then require the portal's saved-draft acknowledgment.
7. Capture a synthetic screenshot or recording reference and return the draft ID and verified values.
8. End the browser session explicitly and record cleanup status.

A typical field operation is `page.locator('[name="insurance_member_id"]').fill(payload['insurance_member_id'])`. A matching verification is `input_value() == payload['insurance_member_id']`. Use the same pattern for all fields. Prefer these stable form selectors for the first version. If you later use Anchor's natural-language task mode, keep the same server validation and approval controls.

Set a short session maximum and idle timeout as backstops. Closing a browser connection does not necessarily terminate the remote session. Use the installed SDK or session API's end-session operation and check the result. Anchor provides recording and replay for its browser sessions; use that evidence for the portal portion of the demo. A desktop recording of the whole EHR application is a separate optional artifact. [R4, R7]

Acceptance check: the portal has a stored draft with exactly the expected patient and member ID, and no simulated receipt exists yet. A model's statement that it finished is not a substitute for the saved record and field read-back.

## 12 Make clinician approval a server rule

Use explicit workflow states: `created`, `running`, `blocked`, `ready_for_review`, `approved`, `submitted_to_mock` and `failed`. Track browser and sandbox cleanup separately so a failed cleanup does not erase a valid draft or masquerade as a clinical error.

The server should calculate a hash of the canonical JSON payload and bind it to task ID, patient ID, context revision and draft revision. The clinician reviews the draft values, source links and limitations, then explicitly approves that revision. An edit, new source revision or changed patient invalidates the approval.

On simulated submission, require that the current payload hash equals the approved hash, the approver session is valid for the demo task, and the request has not already been submitted. The agent cannot create its own approval. Keep the approval credential or nonce out of pre-review worker input.

End the first sandbox after draft creation. Human review can take place against the saved draft. Once approved, start a new short execution if browser work is needed for simulated submission. Rehydrate the exact approved payload, confirm the portal values and submit to the mock route. This avoids keeping a patient sandbox running while the clinician is away.

Use an idempotency key derived from task ID and draft revision. Store one simulated receipt per key. If submission times out, look up the mock request before retrying; a timeout does not prove it failed. Test that direct submission before approval is rejected and that changing the draft after approval requires another review.

## 13 Add Arize traces and measured checks

Use Arize Phoenix for this MVP. Start a collector locally in a separate terminal with `uvx arize-phoenix serve`, or install the server package in a separate environment and use `phoenix serve`. The documented local UI and HTTP collector use port 6006. Set the collector endpoint in `.env` to `http://127.0.0.1:6006/v1/traces`. [R8]

Run the included trace smoke test after installing the integration dependencies:

```bash
python scripts/phoenix_smoke.py --case missing_referral
```

Open the `careflow-demo` project in Phoenix. Confirm a validation span with the selected synthetic patient ID, `blocked` status and the missing-referral error. Export success is useful evidence, but still inspect the collector. This smoke test sends custom validation attributes; it does not install a complete experiment dashboard or run an LLM judge.

In the application, trace context retrieval, sandbox creation, model answer, validation, browser fill, draft verification, approval, simulated submission and cleanup. Register Phoenix tracing and flush the exporter before a short-lived worker exits. To combine parent and worker spans into one trace, propagate OpenTelemetry trace context across the process boundary; otherwise correlate separate traces with the same task ID. Phoenix supports OpenTelemetry tracing and OpenInference instrumentation. [R9]

A cloud sandbox cannot reach your laptop's loopback collector. Use a reachable authenticated collector for worker spans, or have the worker return its event log for the application to record as application-side execution evidence. Label that fallback accurately; it does not capture native worker model spans automatically.

Compute these checks in application code and send the results with the trace: correct patient, valid citations, expected medication values, required documents present, exact form read-back, approval required, single receipt and confirmed cleanup. An intentional missing-document stop is an expected result for that scenario. Record it as a validation block; reserve tool-error status for an actual failed API or browser call.

## 14 Verify the full workflow before the demo

The supplied pack passes eight local reference tests, including six fixed scenario expectations. Cloud provider calls, the application routes, actual model answers, browser completion and approval behavior have not been executed as part of preparing this pack. Verify them in your implementation.

| Check | Evidence to keep |
| --- | --- |
| Selected patient only | Worker input and all returned source IDs belong to the task's patient |
| Source truth | Expected medication values match the dated records and the model claims |
| Missing or conflicting record | Task is blocked and no browser session or receipt is created |
| Draft accuracy | Stored portal values equal the validated payload for every required field |
| Human review | Submission fails before approval and after any unapproved change |
| Retry behavior | Repeated submission requests return one simulated receipt |
| Persistence | A second sandbox retrieves the first attempt's saved outcome from SurrealDB |
| Resource cleanup | Browser termination and sandbox deletion are confirmed or visibly pending retry |
| Trace continuity | Each stage can be located by task ID and its evaluation result inspected |

Also test a model timeout, browser timeout, unreachable portal and failed database write. On each failure save the last durable state, show the specific problem, and clean up resources in a separate recovery path. Do not mark a task complete simply because the model returned text.

Use entirely synthetic records throughout model calls, traces, screenshots and recordings. The prototype does not establish readiness for real clinical use. Security, clinical validation and healthcare-data requirements belong to a separate production design.

## 15 Build and present in this order

Get the accounts, keys, reachable portal and collector checked before a time-limited event. If those integrations are new to you, allow a separate setup session. The following is a three-hour sprint target once that preparation is complete, rather than a guarantee for a first-time integration.

| Time target | Deliverable |
| --- | --- |
| 0 to 20 minutes | Run fixtures, inspect expected cases and seed SurrealDB |
| 20 to 45 minutes | Build patient selection, task status and the mock payer form |
| 45 to 80 minutes | Run selected context and one model question inside Daytona |
| 80 to 105 minutes | Add validation, source links and Phoenix traces |
| 105 to 145 minutes | Fill and verify the mock portal through Anchor |
| 145 to 165 minutes | Add approval enforcement, durable memory and cleanup |
| 165 to 180 minutes | Rehearse the missing-referral recovery and successful run |

Demonstrate Morgan's missing referral, show the validation trace, restore the synthetic document and retry. Review the filled draft, approve it and show a simulated receipt. Delete the execution sandbox, open a new task and show the stored history. Use Avery's medication comparison as the short source-backed clinical question.

If time slips, keep the missing-document stop, draft review and confirmed resource lifecycle. Limit the portal to one form and use the fixed structured inputs. Leave voice, cloud hosting options and broader clinical retrieval until after the core demonstration.

## Official references

The integration instructions were checked against these official pages on 16 October 2026. APIs and package behavior still need the account-level smoke tests above.

- R1 Daytona sandbox lifecycle: https://www.daytona.io/docs/en/sandboxes/
- R2 SurrealDB Docker setup: https://surrealdb.com/docs/running/docker
- R3 SurrealDB HTTP protocol: https://surrealdb.com/docs/reference/rest-api/http-protocol
- R4 Anchor Python SDK quickstart: https://docs.anchorbrowser.io/quickstart/use-via-sdk
- R5 Daytona file transfers: https://www.daytona.io/docs/en/file-system-operations/
- R6 Daytona process execution: https://www.daytona.io/docs/en/python-sdk/sync/process/
- R7 Anchor browser sessions and recording: https://docs.anchorbrowser.io/quickstart/browser-sessions
- R8 Start Arize Phoenix: https://arize.com/docs/phoenix/get-started
- R9 Phoenix tracing setup: https://arize.com/docs/phoenix/get-started/get-started-tracing
