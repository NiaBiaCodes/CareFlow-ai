from types import SimpleNamespace

import pytest

from app.config import Settings
from app.execution import LiveExecution
from app.portal_security import PortalTokens
from app.schemas import BrowserEvent, CreateTask, SaveDraft, SubmitDraft
from app.workflow import Workflow, WorkflowError
from worker.contracts import fact_contract
from .conftest import payload
from .test_approval import approve


def pipeline(repo, timeout_after_save=False, cleanup_failure=False):
    settings = Settings(daytona_key="test", model_key="test", model="test-model", anchor_key="test",
        portal_signing_key="x"*40, payer_url="https://synthetic.example")
    wf = Workflow(repo, settings)
    class Adapter:
        created, deleted, manifests = [], [], []
        fail_cleanup = cleanup_failure
        def __init__(self, settings): pass
        def create(self, tid, purpose="draft"):
            sandbox = SimpleNamespace(id="sandbox-" + str(len(Adapter.created)))
            Adapter.created.append(sandbox)
            return sandbox
        def prepare(self, sandbox): pass
        def execute(self, sandbox, manifest):
            Adapter.manifests.append(manifest)
            assert "nonce" not in manifest and "review_nonce_hash" not in manifest
            if manifest["operation"] == "answer":
                return {"answer": fact_contract(manifest["context"]), "model": "test-model", "response_id": "test-response"}
            access = PortalTokens(settings.portal_signing_key).verify(manifest["portal_token"], manifest["task_id"])
            sid, tid = "browser-" + sandbox.id, manifest["task_id"]
            wf.browser_event(tid, access, BrowserEvent(kind="browser_created", session_id=sid))
            wf.browser_event(tid, access, BrowserEvent(kind="fields_verified", session_id=sid, values=manifest["values"]))
            if manifest["operation"] == "draft":
                draft = wf.save(tid, SaveDraft.model_validate(payload(wf.view(tid))), access)
                wf.browser_event(tid, access, BrowserEvent(kind="browser_closed", session_id=sid))
                if timeout_after_save: raise TimeoutError()
                return {"verified_values": draft["values"]}
            request = SubmitDraft(task_id=tid, patient_id=manifest["patient_id"], context_hash=wf.view(tid)["context_hash"],
                draft_hash=manifest["draft_hash"], draft_revision=manifest["draft_revision"], values=manifest["values"])
            receipt = wf.submit(tid, request, access)
            wf.browser_event(tid, access, BrowserEvent(kind="browser_closed", session_id=sid))
            return {"receipt_id": receipt["receipt_id"], "verified_values": manifest["values"]}
        def delete(self, sandbox):
            if Adapter.fail_cleanup: raise TimeoutError()
            Adapter.deleted.append(sandbox.id)
        def journal(self, sandbox): return None
        def find(self, task_id): return [s for s in Adapter.created if s.id not in Adapter.deleted]
    task = wf.create(CreateTask(patient_id="SYN-P001", execution_mode="live"))
    return wf, LiveExecution(wf, Adapter), task["task_id"], Adapter


def test_full_orchestration_contract_uses_separate_short_executions(repo):
    wf, service, tid, adapter = pipeline(repo)
    service.run(tid)
    assert wf.view(tid)["status"] == "draft_saved"
    assert len(adapter.created) == len(adapter.deleted) == 1
    approve(wf, tid)
    service.submit(tid)
    saved = wf.view(tid)
    assert saved["status"] == "submitted_to_mock" and saved["receipt"]["label"] == "Simulated receipt"
    assert [m["operation"] for m in adapter.manifests] == ["answer", "draft", "submit"]
    assert len(adapter.created) == len(adapter.deleted) == 2
    service.submit(tid)
    assert len(adapter.created) == 2


def test_timeout_after_click_recovers_draft_without_duplicate_write(repo):
    wf, service, tid, adapter = pipeline(repo, timeout_after_save=True)
    service.run(tid)
    saved = wf.view(tid)
    assert saved["status"] == "draft_saved" and saved["acknowledgment_recovered"] is True
    assert len(saved["drafts"]) == 1 and len(adapter.deleted) == 1


def test_cleanup_failure_is_durable_and_blocks_review(repo, monkeypatch):
    wf, service, tid, adapter = pipeline(repo, cleanup_failure=True)
    service.run(tid)
    assert wf.view(tid)["executions"][0]["sandbox_cleanup"] == "cleanup_pending"
    with pytest.raises(WorkflowError, match="CLEANUP_REQUIRED"): wf.review(tid)
    adapter.fail_cleanup = False
    # Browser was already confirmed ended; avoid any real provider call in this unit test.
    service.settings = Settings(daytona_key="test")
    service.recover(tid)
    assert wf.view(tid)["executions"][0]["sandbox_cleanup"] == "deleted"


def test_journal_failure_does_not_skip_sandbox_deletion(repo):
    wf, service, tid, adapter = pipeline(repo)
    def broken(self, sandbox): raise TimeoutError()
    adapter.journal = broken
    service.run(tid)
    assert adapter.deleted
    assert wf.view(tid)["executions"][0]["journal_recovery_failed"] is True


def test_server_ends_browser_from_pending_journal_before_deleting_sandbox(repo, monkeypatch):
    wf, service, tid, adapter = pipeline(repo)
    adapter.journal = lambda self, sandbox: {"session_id": "browser-" + sandbox.id, "cleanup": "cleanup_pending"}
    ended = []
    monkeypatch.setattr("anchorbrowser.Anchorbrowser", lambda **kwargs: object())
    def close(client, sid):
        assert not adapter.deleted
        ended.append(sid)
        return True
    monkeypatch.setattr("app.cleanup.end_session", close)
    service.run(tid)
    saved = wf.view(tid)
    assert ended == ["browser-sandbox-0"]
    assert saved["executions"][0]["browser_sessions"][0]["cleanup"] == "ended"
    assert adapter.deleted == ["sandbox-0"]
