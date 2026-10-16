import copy
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.execution import LiveExecution
from app.schemas import CreateTask
from app.workflow import Workflow
from core import patient_bundle
from worker.contracts import fact_contract, validate_answer


def test_model_contract_checks_values_not_just_citation_existence(dataset):
    bundle = patient_bundle(dataset, "SYN-P001")
    answer = fact_contract(bundle)
    assert validate_answer(answer, bundle, "SYN-P001")["medications"][0]["after"]["dose"] == "20 mg"
    for key, value in [("dose", "5 mg"), ("frequency", "twice daily")]:
        bad = copy.deepcopy(answer)
        bad["medications"][0]["after"][key] = value
        with pytest.raises(ValueError):
            validate_answer(bad, bundle, "SYN-P001")


@pytest.mark.parametrize("kind", ["patient", "citation", "missing_med", "pharmacy", "extra", "malformed"])
def test_model_malformed_unsupported_outputs(dataset, kind):
    bundle = patient_bundle(dataset, "SYN-P001")
    answer = fact_contract(bundle)
    if kind == "patient": answer["patient_id"] = "SYN-P002"
    if kind == "citation": answer["medications"][0]["source_record_ids"] = ["NOTE-002-2"]
    if kind == "missing_med": answer["medications"].pop()
    if kind == "pharmacy": answer["fills"] = "none"
    if kind == "extra": answer["diagnosis"] = "unsupported"
    if kind == "malformed": answer = {"patient_id": "SYN-P001"}
    with pytest.raises(ValueError): validate_answer(answer, bundle, "SYN-P001")


def test_selected_context_crosses_boundary_and_cleanup_on_failure(repo):
    wf = Workflow(repo, Settings(daytona_key="test", model_key="test", model="test-model"))
    task = wf.create(CreateTask(patient_id="SYN-P001", execution_mode="live"))
    class Adapter:
        deleted = False
        def __init__(self, settings): pass
        def create(self, tid): return SimpleNamespace(id="test-sandbox")
        def prepare(self, sandbox): pass
        def execute(self, sandbox, manifest):
            text = str(manifest)
            assert "SYN-P002" not in text and "expectations" not in text
            assert "password" not in text and "approval" not in text
            raise TimeoutError()
        def delete(self, sandbox): Adapter.deleted = True
    LiveExecution(wf, Adapter).run(task["task_id"])
    saved = repo.get_task(task["task_id"])
    assert saved["status"] == "failed" and saved["live_answer"] is None
    assert Adapter.deleted and saved["executions"][0]["sandbox_cleanup"] == "deleted"


def test_missing_configuration_is_a_saved_failure_not_reference_fallback(repo):
    wf = Workflow(repo, Settings())
    task = wf.create(CreateTask(patient_id="SYN-P001", execution_mode="live"))
    LiveExecution(wf).run(task["task_id"])
    saved = repo.get_task(task["task_id"])
    assert saved["status"] == "configuration_missing" and saved["live_answer"] is None
    assert "DAYTONA_API_KEY" in saved["configuration_missing"]


def test_failed_worker_telemetry_and_safe_reason_survive_cleanup(repo):
    from app.daytona_adapter import WorkerFailure
    wf = Workflow(repo, Settings(daytona_key="test", model_key="test", model="test"))
    task = wf.create(CreateTask(patient_id="SYN-P001", execution_mode="live"))
    class Adapter:
        def __init__(self, settings): pass
        def create(self, tid): return SimpleNamespace(id="sandbox")
        def prepare(self, sandbox): pass
        def execute(self, sandbox, manifest):
            raise WorkerFailure({"error": "PORTAL_CONNECTION_FAILED_CHECK_DAYTONA_EGRESS",
                "telemetry": {"status": "export_failed", "trace_id": "worker-trace"}})
        def delete(self, sandbox): pass
    LiveExecution(wf, Adapter).run(task["task_id"])
    saved = wf.view(task["task_id"])
    assert saved["execution_error"] == "PORTAL_CONNECTION_FAILED_CHECK_DAYTONA_EGRESS"
    assert saved["executions"][0]["failed_worker_telemetry"]["status"] == "export_failed"
    assert saved["executions"][0]["sandbox_cleanup"] == "deleted"
    assert saved["live_answer"] is None
