async function sendJSON(url, body) {
  const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || JSON.stringify(result.detail));
  return result;
}
const create = document.querySelector("#create-task");
if (create) {
  const patient = create.querySelector("#patient"), scenario = create.querySelector("#scenario");
  function filterScenarios() {
    if (!scenario) return;
    for (const option of scenario.options) {
      option.hidden = option.dataset.patient && option.dataset.patient !== patient.value;
      option.disabled = Boolean(option.hidden);
    }
    scenario.value = "";
  }
  patient.addEventListener("change", filterScenarios);
  filterScenarios();
  create.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = create.querySelector("button"), error = document.querySelector("#task-error");
    button.disabled = true; error.textContent = "";
    try {
      const task = await sendJSON("/api/tasks", {patient_id: patient.value, scenario: scenario?.value || null,
        execution_mode: create.querySelector('#execution-mode').value});
      window.location.assign(`/tasks/${encodeURIComponent(task.task_id)}`);
    } catch (e) { error.textContent = e.message; button.disabled = false; }
  });
}
const payer = document.querySelector("#payer-form");
if (payer) payer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = document.querySelector('[data-testid="draft-status"]'), button = payer.querySelector("button");
  const values = Object.fromEntries(new FormData(payer));
  const taskID = values.task_id; delete values.task_id;
  values.requested_units = Number(values.requested_units);
  button.disabled = true; status.textContent = "Saving…";
  try {
    const draft = await sendJSON(`/mock-payer/requests/${encodeURIComponent(payer.dataset.task)}/draft`, {
      task_id: taskID, patient_id: values.patient_id, context_hash: payer.dataset.context,
      expected_revision: Number(payer.dataset.revision), values
    });
    payer.dataset.revision = String(draft.revision);
    payer.dataset.draftHash = draft.draft_hash;
    status.textContent = `Saved draft revision ${draft.revision} · ${draft.draft_id}`;
  } catch (e) { status.textContent = `Save rejected: ${e.message}`; }
  finally { button.disabled = false; }
});
const approveDraft = document.querySelector('#approve-draft');
if (approveDraft) approveDraft.addEventListener('click', async () => {
  approveDraft.disabled = true;
  try {
    await sendJSON(`/api/tasks/${approveDraft.dataset.task}/approval`, {
      nonce: approveDraft.dataset.nonce, context_hash: approveDraft.dataset.context,
      draft_hash: approveDraft.dataset.hash, draft_revision: Number(approveDraft.dataset.revision)
    });
    window.location.reload();
  } catch (e) { document.querySelector('#live-error').textContent = e.message; approveDraft.disabled = false; }
});
const executeSubmit = document.querySelector('#execute-submit');
if (executeSubmit) executeSubmit.addEventListener('click', async () => {
  executeSubmit.disabled = true;
  try { await sendJSON(`/api/tasks/${executeSubmit.dataset.task}/execute-submit`, {}); window.location.reload(); }
  catch (e) { document.querySelector('#live-error').textContent = e.message; executeSubmit.disabled = false; }
});
const liveState = document.querySelector('#live-state');
for (const button of document.querySelectorAll('.live-action')) button.addEventListener('click', async () => {
  button.disabled = true;
  try { await sendJSON(`/api/tasks/${button.dataset.task}/${button.dataset.action}`, {}); window.location.reload(); }
  catch (e) { document.querySelector('#live-error').textContent = e.message; button.disabled = false; }
});
if (liveState) {
  async function pollTask() {
    try {
      const response = await fetch(`/api/tasks/${liveState.dataset.task}`);
      if (!response.ok) throw new Error('Task refresh failed');
      const task = await response.json();
      if (task.status !== liveState.dataset.status || String(task.event_sequence) !== liveState.dataset.sequence) {
        window.location.reload(); return;
      }
    } catch (e) { document.querySelector('#live-error').textContent = e.message; }
    setTimeout(pollTask, 2500);
  }
  setTimeout(pollTask, 2500);
}
const submitPayer = document.querySelector('#submit-payer');
if (submitPayer) submitPayer.addEventListener('click', async () => {
  const values = Object.fromEntries(new FormData(payer)), taskID = values.task_id;
  delete values.task_id; values.requested_units = Number(values.requested_units);
  const receipt = document.querySelector('[data-testid="mock-receipt"]');
  submitPayer.disabled = true;
  try {
    const saved = await sendJSON(`/mock-payer/requests/${payer.dataset.task}/submit`, {
      task_id: taskID, patient_id: values.patient_id, context_hash: payer.dataset.context,
      draft_revision: Number(payer.dataset.revision), draft_hash: payer.dataset.draftHash, values
    });
    receipt.dataset.receiptId = saved.receipt_id;
    receipt.textContent = `Simulated receipt ${saved.receipt_id}`;
  } catch (e) { receipt.textContent = `Submission rejected: ${e.message}`; submitPayer.disabled = false; }
});
