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
      const task = await sendJSON("/api/tasks", {patient_id: patient.value, scenario: scenario?.value || null});
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
    status.textContent = `Saved draft revision ${draft.revision} · ${draft.draft_id}`;
  } catch (e) { status.textContent = `Save rejected: ${e.message}`; }
  finally { button.disabled = false; }
});
