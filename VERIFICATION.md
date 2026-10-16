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
