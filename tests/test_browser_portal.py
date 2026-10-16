from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.edge import PortalBoundary
from app.execution import LiveExecution
from app.main import create_app
from app.portal_security import PortalTokens
from app.schemas import CreateTask
from app.workflow import Workflow
from worker.contracts import fact_contract
from worker.browser import drive, end_session
from .conftest import payload


def test_public_boundary_denies_private_routes_and_requires_task_capability(repo):
    settings = Settings(portal_signing_key="x" * 40)
    wf = Workflow(repo, settings)
    task = wf.create(CreateTask(patient_id="SYN-P001"))
    token = PortalTokens(settings.portal_signing_key).mint(task["task_id"], "execution", "draft")
    with TestClient(PortalBoundary(create_app(repo, settings))) as edge:
        for path in ("/", "/api/patients", "/docs", "/openapi.json", f'/api/tasks/{task["task_id"]}/approval'):
            assert edge.get(path).status_code == 404
        url = f'/mock-payer/requests/{task["task_id"]}'
        assert edge.get(url).status_code == 403
        assert edge.get(url, headers={"Authorization": "Bearer " + token}).status_code == 200
        assert edge.get('/mock-payer/requests/' + '0'*36, headers={"Authorization": "Bearer " + token}).status_code == 403
        assert edge.get("/static/style.css").status_code == 200


def test_missing_referral_stops_before_browser_creation(repo):
    wf = Workflow(repo, Settings(daytona_key="test", model_key="test", model="test"))
    task = wf.create(CreateTask(patient_id="SYN-P002", scenario="missing_referral", execution_mode="live"))
    class Adapter:
        operations = []
        def __init__(self, settings): pass
        def create(self, tid): return SimpleNamespace(id="test-sandbox")
        def prepare(self, sandbox): pass
        def execute(self, sandbox, manifest):
            Adapter.operations.append(manifest["operation"])
            return {"answer": fact_contract(manifest["context"]), "model": "test", "response_id": "test-response"}
        def delete(self, sandbox): pass
    LiveExecution(wf, Adapter).run(task["task_id"])
    saved = wf.view(task["task_id"])
    assert saved["status"] == "blocked" and saved["live_answer"]
    assert Adapter.operations == ["answer"] and saved["drafts"] == []


class Node:
    def __init__(self, page, selector): self.page, self.selector = page, selector
    def get_attribute(self, key): return self.page.manifest["task_id"]
    def input_value(self):
        name = self.selector.split('"')[1]
        if name == self.page.mismatch: return "wrong"
        return str(self.page.manifest["values"][name])
    def fill(self, value): pass
    def click(self):
        self.page.clicks.append(self.selector)
        if self.page.timeout: raise TimeoutError()
    def filter(self, **kwargs): return self
    def wait_for(self, **kwargs): pass
    def inner_text(self): return "Saved draft revision 1"


class Page:
    def __init__(self, manifest, mismatch=None, timeout=False):
        self.manifest, self.mismatch, self.timeout, self.clicks = manifest, mismatch, timeout, []
    def set_default_timeout(self, value): pass
    def goto(self, *args, **kwargs): return SimpleNamespace(status=200)
    def locator(self, selector): return Node(self, selector)
    def get_by_test_id(self, selector): return Node(self, selector)
    def reload(self, **kwargs): pass


def manifest_for(task):
    return {"task_id": task["task_id"], "patient_id": task["patient_id"], "operation": "draft",
            "payer_url": "https://synthetic.example", "values": task["validation"]["form_fields"], "session_id": "test-session"}


def test_browser_reads_all_fields_before_click_and_rejects_mismatch(repo, monkeypatch):
    wf = Workflow(repo, Settings())
    task = wf.create(CreateTask(patient_id="SYN-P001"))
    events = []
    monkeypatch.setattr("worker.browser.callback", lambda *args: events.append(args[1]))
    for field in task["validation"]["form_fields"]:
        page = Page(manifest_for(task), mismatch=field)
        with pytest.raises(ValueError): drive(page, page.manifest)
        assert not page.clicks
    page = Page(manifest_for(task))
    result = drive(page, page.manifest)
    assert page.clicks == ["save-draft"] and events == ["fields_verified"]
    assert result["verified_values"] == task["validation"]["form_fields"]


def test_browser_timeout_is_not_a_success(repo, monkeypatch):
    task = Workflow(repo, Settings()).create(CreateTask(patient_id="SYN-P001"))
    monkeypatch.setattr("worker.browser.callback", lambda *args: None)
    page = Page(manifest_for(task), timeout=True)
    with pytest.raises(TimeoutError): drive(page, page.manifest)


def test_browser_deletion_requires_confirmation(monkeypatch):
    monkeypatch.setattr("worker.browser.time.sleep", lambda _: None)
    sessions = SimpleNamespace(delete_session=lambda *a, **k: None,
        get_session=lambda *a, **k: SimpleNamespace(data=SimpleNamespace(status="active")))
    assert end_session(SimpleNamespace(sessions=sessions), "test") is False
