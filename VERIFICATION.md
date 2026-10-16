# PR1 verification

Completed 16 October 2026 using Python 3.12.14 on Windows and a real Docker
SurrealDB v2.3.10 server. The server image digest was
`sha256:e3322f21b3135dd4893e6487874481333b03bc731d7da200d716a8564b4f0a92`.
Exact installed Python dependencies are recorded in `requirements.lock.txt`.

## Automated results

| Check | Result |
| --- | --- |
| `python scripts/check_data.py` | 8 original checks passed |
| `CAREFLOW_RUN_DB_TESTS=1 python -m pytest -q` | 43 passed, including 2 real SurrealDB tests |
| Fixtures and original reference assets compared byte for byte with starter ZIP | 18 files unchanged |
| `.env` and `.venv` excluded by git | Confirmed |

The application tests cover the six scenario outcomes, patient context and source
access isolation, directory/history summaries, expected medication doses and
citations, distinct effective dates, agreeing duplicates and conflicting records,
complete reconciliations, strict types, timestamps, signed notes, referrals,
encounter/provider/service relationships, draft-write blocks, substitutions,
unsupported changes, revision checks, concurrent saves, append-only histories,
escaped source text, demo-mode enforcement, storage failures, and server-side
submission/approval rejection.

The real database tests create unique `pr1_test_<uuid>` databases, load the baseline,
save and recover tasks/drafts through fresh repository and application instances,
reseed without losing workflow history, exercise all six scenarios without changing
baseline charts, and race two draft saves. Exactly one concurrent save succeeds;
the other receives HTTP 409, with no extra draft/event. Both disposable databases
were removed after each run. These checks use real HTTP queries, not memory storage.

One upstream Starlette warning remains: its TestClient deprecates the current
`httpx` backend in favor of `httpx2`. It did not affect the passing checks.

## Browser and restart results

The in-app browser exercised the live application at `http://127.0.0.1:8000`:

- Selected Avery and viewed both encounters, effective/recorded medication times,
  lisinopril 10 mg to 20 mg, unchanged metformin, and clickable record citations.
- Opened the mock payer page, verified named fields and source links, clicked
  **Save draft**, and observed revision 1 plus the explicit no-receipt state.
- Reloaded the portal and confirmed that saved revision 1 remained visible.
- Selected Morgan with `missing_referral`; observed unchanged amlodipine,
  `MISSING_REFERRAL`, and no available draft action.
- Selected `corrected_referral`; observed a separate ready-for-review task and the
  earlier failed task retained in history.
- Opened `NOTE-001-2` and inspected its source text, signed/final status, patient ID,
  encounter relationship, effective time, and recorded time.

The Uvicorn process was then stopped and replaced, and the CareFlow SurrealDB
container was restarted using its persistent volume. An independent HTTP read and
browser reload recovered the existing Avery task, draft revision 1, exact member
ID, and three workflow events. No reseed or fallback was used for recovery.

Live demonstration evidence:

- Task: `6570d9b2-9d7f-4007-bbc4-c16b59fcefa8`
- Saved draft: `c37fcab1-f792-4f7e-a155-476c7b5ba1eb`
- Missing-referral task: `f4a96861-e83a-4428-85d3-91fc2124c4b9`
- Corrected-referral task: `d6093b33-9e11-4a6b-851e-adc706a1df2e`
- Screenshot after restart: `verification/saved-draft.jpg`

## Scope

The application and database remain available locally for review. A random local
database password was generated in the ignored `.env`; no credentials were placed
in tracked configuration. No model, Daytona, Anchor, Phoenix cloud, ElevenLabs,
or AWS service was invoked. Approval and submission remain unavailable in PR1.

All requested PR1 checks were completed. Broader integration behavior in the
original roadmap remains work for later phases.

## Repository relocation

Moved the application into the root of C:\Users\piush\Desktop\CareFlow-ai on 16 October 2026, preserving the existing Git remote and ignored local configuration. Refreshed the virtual environment entrypoints for the new path. All 43 application tests, including the real SurrealDB tests, and all eight reference checks passed from the new checkout. The restarted app returned HTTP 200 and recovered the existing saved draft. The original workspace copy was removed. Compose now has an explicit project name to preserve its existing database volume across relocation.

# Integrated demo verification — 16 October 2026

The PR1 results above are historical. This extension adds and **live-verifies**
Daytona, OpenAI, Anchor, local Arize Phoenix, approval, simulated submission, and
cleanup. No commit, push, AWS deployment, real EHR, or real payer was used.

## Final automated checks

| Check | Result |
| --- | --- |
| `CAREFLOW_RUN_DB_TESTS=1 python -m pytest -q --tb=short` | **74 passed**, including **3 real SurrealDB tests** |
| `python scripts/check_data.py` | **8 original checks passed** |
| `node --check app/static/app.js` | Passed |
| `git diff --check` | Passed after normalizing generated lockfile line endings |
| Configured secret values in reviewable text files | Checked separately; no credentials included in evidence |

New coverage exercises patient ownership across the upload boundary, exact facts
and citation sets, malformed model results, missing-referral blocking before
browser creation, every required browser field mismatch, browser timeouts,
approval enforcement, changed draft/context, concurrent duplicate submission,
cleanup after failure, recovery, failed telemetry persistence, and trace continuity
through cleanup. A subprocess executes the actual uploaded whitelist independently
of the source checkout. These tests use provider fakes where specified; they are
not counted as live provider proof.

The installed SDKs inspected and used were Daytona **0.224.1**, OpenAI **3.31.0**,
Anchor Browser **1.0.1**, Playwright **1.64.0**, and Phoenix OTEL **0.17.2**.
The runtime model was selected from the account's actual model list:
`gpt-4.1-mini-2025-04-14`. Exact dependencies are pinned in the lockfile and remote
worker requirements. Phoenix **20.23.0** runs locally from image digest
`sha256:c542e7de1dc1fdd0f938de337d44ebefa7266e91391eed6e7978523d7f6e1f2f`.

## Real synthetic execution evidence

| Run | Task | Result |
| --- | --- | --- |
| Morgan missing referral | `6a90653b-58dd-4da8-b352-33ae1acef42a` | Actual model call inside Daytona; amlodipine 5 mg daily unchanged with MED-002-1/2; administrative `MISSING_REFERRAL` block; no browser or draft |
| Morgan corrected referral | `7c166f9a-7ae2-4320-9059-a1ec41d669bd` | Actual model + Anchor draft, exact UI approval, new Daytona/Anchor submission, one simulated receipt |
| Avery happy | `7aeb5bb2-e244-4f82-8458-ef5905220a54` | Actual model reports lisinopril 10 mg to 20 mg, metformin 500 mg twice daily unchanged, cited MED-001-1/2; Anchor draft, exact UI approval, new Daytona/Anchor submission |

Morgan's draft sandbox `462f922e-0070-4968-be91-542e6e445a76` and Anchor session
`ea1523ef-92c4-43c1-8d0a-684ddc849d91` saved draft
`7fe52ffb-d982-4f15-b33e-3326783d024f`, revision 1. Both resources ended **before**
the private clinician review. The UI approval bound context, patient, revision,
and exact hash. Submission used a new sandbox
`40096a68-5a6d-43ba-9191-613e36267aeb` and browser
`1dc51895-670c-4da9-b3b8-695bbc538ce5`, yielding
**SIM-abab8f638bac389441e6a216**.

Avery's draft sandbox `824219fd-a246-4930-ad26-d98206a9cebe` and browser
`e003fd4c-6e17-48c3-ba33-8794b056a4b2` saved draft
`71a09a77-95e2-4898-81d4-f88d2df59d55`, revision 1. Submission used sandbox
`cd214419-1b92-4fd7-a68f-b40d318e7934` and browser
`c2b8127d-6213-4d90-8335-fb45fd66e880`, yielding
**SIM-fbd84e0f104c16b5a19ea3d6**.

The worker actually connected to Anchor, opened the restricted HTTPS portal,
verified the patient/task, filled and read all eleven required values, clicked
Save through the DOM, and reloaded the saved acknowledgment. After approval it
reopened the draft, checked every value plus revision/hash, clicked simulated
Submit, and reloaded the receipt. No direct API draft save or receipt fabrication
substituted for those browser actions.

Three repeated private submission requests for Morgan returned the identical
saved receipt with **zero new executions** and exactly **one submission event**.
The real SurrealDB test additionally races duplicate submissions to exercise the
atomic receipt transaction. No real insurer submission occurred.

## Phoenix, failures, and recovery

Real visible Phoenix validation trace:
`061929e61bf920457351835d2ce918c4` — task ID, supported facts/citations `true`,
required documents `false`, validation result `blocked`. This is an expected
administrative block, not an execution error. Earlier API/connection failures
appear as ERROR spans and remain in task history.

Morgan draft trace `d971364ac77674a3c0e8ade67aa7479a` contains application and
worker model/browser activity with the same propagated trace ID. Avery draft
trace is `8c5b29236490168224e19d367e36e4b7`. Avery's final submission trace
`8349d7d46aa0a1d43d250d3fadbf97ba` correlates the application, worker submission,
approval/receipt checks, and cleanup. Worker telemetry flushed before sandbox
deletion; final successful runs report exported application and worker spans.

An earlier Morgan submission captured cleanup in a separate trace
`3c0df815953105ccc787326b321eecb7`, while submission/worker activity used
`85c465056a82f920d526b2b63fbcc256`. This led to the final trace-continuity fix and
regression test; Avery's later live run verifies the fix. Historical trace records
are retained accurately rather than rewritten.

Initial verification also found Windows TLS inspection trust missing from the
Python client, restricted Daytona outbound access, an unsupported model response,
one timed-out sandbox creation, and Anchor HTTP 402 insufficient credits. TLS
verification stayed enabled using existing Windows roots. The user enabled
Daytona outbound access and added Anchor credits. Recovery discovered and deleted
the sandbox whose creation had timed out before its ID reached the app. Invalid
model output never became a reference answer. Worker export failures before
network access was enabled remain explicitly `export_failed` in saved history.
The standalone Anchor connectivity probe is labelled separately and is not used
as evidence of the actual Daytona browser workflow.

After Morgan submission, the local application was stopped and restarted with
fresh processes. Reloading its task recovered the same receipt and complete
history from SurrealDB, with the execution resources already gone. A later Avery
task also recovered the original PR1 task in saved history. Independent fresh
repository reads recovered both final outcomes without a live sandbox.

## Cleanup and remaining runtime

The final provider audit queried Daytona by each task label and Anchor by each
task tag. For all three live tasks it found **zero remaining Daytona sandboxes**
and **zero active Anchor sessions**. Stored execution records show confirmed
deletion/termination, including failed attempts and the standalone diagnostic.
No cleanup is pending. Detailed, credential-free evidence is saved in
`verification/live-evidence.json`.

The local clinician app (8000), restricted payer proxy (8002), authenticated OTLP
relay (8003), SurrealDB (8001), Phoenix UI/collector (6006), and two temporary
Cloudflare tunnels remain available for review. Two unused alternate SSH tunnels
were stopped. The tunnel executable is in ignored `runs/tools`; no provider
resource is held during human review. Private routes and missing capabilities
were checked through the public boundary and rejected with 404/403.

No configuration blocker remains. The demonstration needs a funded Anchor
account and Daytona outbound access, and temporary tunnel URLs must be refreshed
after restart. The local app deliberately uses one worker and explicit interrupted
job recovery. See `README.md` for exact startup commands and configuration.

Screenshots: `verification/live-simulated-receipt.jpg`,
`verification/avery-simulated-receipt.jpg`,
`verification/phoenix-referral-validation.jpg`, and
`verification/phoenix-approved-submission.jpg`. The final Phoenix screenshot
shows approval enforced, exact form values, and single receipt checks all true,
with the worker and cleanup visible in the same trace tree.
