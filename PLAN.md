# Integrated demo milestone

The PR1 application remains the foundation. No parallel application, agent
framework, fixture regeneration, commit, push, or deployment was introduced.

| Milestone | Implementation and verification |
| --- | --- |
| Inspect and preserve PR1 | Existing UI, data, six scenarios, repository interfaces, evidence validation, and history preserved; original checks pass |
| 1. Daytona and live model | Selected context and code whitelist uploaded; real OpenAI Responses calls inside Daytona; strict fact/citation checks in worker and server; both synthetic patients verified |
| 2. Phoenix | Native OTLP application and worker spans, W3C parent propagation, validation checks, explicit export failures, and flush before deletion; real visible traces verified |
| 3. Anchor draft | Real Anchor browser in Daytona verifies task/patient, fills and reads all fields, clicks Save, reloads persisted acknowledgment; Morgan and Avery drafts verified |
| 4. Approval and submission | Exact context/revision/hash plus private one-time nonce; new short browser execution after approval; Morgan and Avery simulated receipts verified; repeated requests return the saved receipt |
| 5. Durable history and cleanup | Atomic SurrealDB transitions, append-only failed attempts, recoverable resource IDs, explicit Anchor termination and confirmed Daytona deletion; restart recovery verified |
| Final handoff | Complete: both patients submitted; provider audit found zero remaining Daytona sandboxes and zero active Anchor sessions for all three live demo tasks |

The full regression suite passes 74 tests, including three real SurrealDB tests,
plus all eight original checks. The remote worker whitelist was also executed in
a real local subprocess test. Unit adapters deliberately use fakes for malformed
model output, field mismatches/timeouts, approval failures, concurrency, and
cleanup recovery. Those tests are separate from actual provider evidence.

Account/network issues found during verification were handled explicitly: the
Windows Python client needed existing trusted TLS roots; Daytona outbound access
needed enabling; Anchor initially reported insufficient credits. The user fixed
the provider account requirements. The final integrated runs use real providers,
with no reference fallback or local browser substitute.

See `README.md` for startup/configuration and the shortest demo sequence.
`VERIFICATION.md` and `verification/live-evidence.json` record actual task,
execution, browser, trace, draft, receipt, and cleanup evidence.

Deferred as requested: ElevenLabs, AWS deployment, broad clinical search, real EHR
and insurer connections. The local app uses one Uvicorn worker, not a distributed
job queue; interrupted paid jobs require explicit recovery. Temporary tunnel
addresses must be refreshed after restart. This is a synthetic demonstration.
