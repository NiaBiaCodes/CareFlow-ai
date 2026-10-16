from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import Settings
from app.schemas import ApproveDraft, BrowserEvent, CreateTask, SaveDraft, SubmitDraft
from app.workflow import Workflow, WorkflowError, digest
from worker.contracts import fact_contract
from .conftest import payload
from .support import MemoryRepository


def verified_draft(repo, settings=None):
    """Test setup only; this does not count as a real browser/provider verification."""
    wf = Workflow(repo, settings or Settings())
    task = wf.create(CreateTask(patient_id="SYN-P001", execution_mode="live"))
    context = repo.get_task(task["task_id"])["context_snapshot"]
    def model_validated(t):
        t.update(status="ready_for_review", live_answer={"answer": fact_contract(context)},
            executions=[{"execution_id": "draft-execution", "operation": "draft", "sandbox_id": "test-sandbox",
                "sandbox_cleanup": "pending", "browser_sessions": []}])
    wf.change(task["task_id"], "test_model_validated", model_validated)
    access = {"scope": "draft", "execution_id": "draft-execution"}
    wf.browser_event(task["task_id"], access, BrowserEvent(kind="browser_created", session_id="test-browser"))
    wf.browser_event(task["task_id"], access, BrowserEvent(kind="fields_verified", session_id="test-browser",
        values=task["validation"]["form_fields"]))
    wf.save(task["task_id"], SaveDraft.model_validate(payload(task)), access)
    def cleanup(t):
        t["executions"][0]["sandbox_cleanup"] = "deleted"
        t["executions"][0]["browser_sessions"][0]["cleanup"] = "ended"
    wf.change(task["task_id"], "test_cleanup", cleanup)
    return wf, task["task_id"]


def approve(wf, tid):
    review = wf.review(tid)
    wf.approve(tid, ApproveDraft(nonce=review["nonce"], context_hash=review["context_hash"],
        draft_hash=review["draft_hash"], draft_revision=review["draft_revision"]))


def submission(wf, tid):
    task = wf.repo.get_task(tid)
    binding = wf.approved_binding(task)
    def begin(t):
        t["status"] = "submission_running"
        t["executions"].append({"execution_id": "submit-execution", "operation": "submit",
            "sandbox_id": "test-submit-sandbox", "sandbox_cleanup": "pending", "browser_sessions": [],
            "approval_binding_hash": digest(binding)})
    wf.change(tid, "test_submission_started", begin)
    access = {"scope": "submit", "execution_id": "submit-execution"}
    wf.browser_event(tid, access, BrowserEvent(kind="browser_created", session_id="submit-browser"))
    wf.browser_event(tid, access, BrowserEvent(kind="fields_verified", session_id="submit-browser",
        values=task["validation"]["form_fields"]))
    request = SubmitDraft(task_id=tid, patient_id=task["patient_id"], context_hash=task["context_hash"],
        draft_hash=task["draft_hash"], draft_revision=task["draft_revision"], values=task["validation"]["form_fields"])
    return request, access


def test_approval_requires_clinician_nonce_exact_revision_and_cleaned_resources(repo):
    wf, tid = verified_draft(repo)
    review = wf.review(tid)
    for override in ({"nonce": "wrong"}, {"draft_revision": 2}, {"draft_hash": "different"}):
        fields = {k: review[k] for k in ("nonce", "draft_revision", "draft_hash", "context_hash")}
        fields.update(override)
        with pytest.raises(WorkflowError): wf.approve(tid, ApproveDraft(**fields))
        assert not repo.get_task(tid).get("approval")
    approve(wf, tid)
    assert repo.get_task(tid)["status"] == "approved"
    assert "review_nonce_hash" not in wf.view(tid)


def test_submission_before_approval_and_wrong_capability_are_rejected(repo):
    wf, tid = verified_draft(repo)
    task = repo.get_task(tid)
    with pytest.raises(WorkflowError): wf.approved_binding(task)
    with pytest.raises(WorkflowError): wf.submit(tid, None, None)
    with pytest.raises(WorkflowError): wf.submit(tid, None, {"scope": "draft"})
    assert not repo.get_task(tid).get("receipt")


def test_new_saved_revision_invalidates_approval(repo):
    wf, tid = verified_draft(repo)
    approve(wf, tid)
    wf.save(tid, SaveDraft.model_validate(payload(wf.view(tid))))
    task = repo.get_task(tid)
    assert task["draft_revision"] == 2 and task["approval"] is None
    with pytest.raises(WorkflowError): wf.approved_binding(task)


def test_source_change_after_approval_requires_new_review(repo):
    wf, tid = verified_draft(repo)
    approve(wf, tid)
    repo.store.dataset["documents"][0]["text"] += " Revised source."
    with pytest.raises(WorkflowError, match="SOURCE_RECORDS_CHANGED"):
        wf.approved_binding(repo.get_task(tid))
    assert repo.get_task(tid)["status"] == "stale_context"
    assert repo.get_task(tid)["approval"] is None


def test_saved_draft_tampering_cannot_retain_approval(repo):
    wf, tid = verified_draft(repo)
    approve(wf, tid)
    repo.store.drafts[tid][-1]["values"]["requested_units"] = 99
    with pytest.raises(WorkflowError, match="DRAFT_HASH_MISMATCH"):
        wf.approved_binding(repo.get_task(tid))


def test_duplicate_and_concurrent_submission_return_one_durable_receipt(repo):
    wf, tid = verified_draft(repo)
    approve(wf, tid)
    request, access = submission(wf, tid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: wf.submit(tid, request, access), range(2)))
    assert results[0] == results[1]
    assert results[0]["label"] == "Simulated receipt"
    assert sum(e["kind"] == "submitted_to_mock" for e in repo.get_events(tid)) == 1
    fresh = Workflow(MemoryRepository(repo.store), Settings())
    assert fresh.view(tid)["receipt"] == results[0]
    assert fresh.submit(tid, request, access) == results[0]
