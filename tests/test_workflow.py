import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.main import create_app
from app.repository import StorageUnavailable
from app.validation import evaluate
from core import patient_bundle
from .conftest import create, payload
from .support import MemoryRepository


def test_six_scenarios_and_baseline_remain_intact(client, repo, dataset):
    expectations = json.loads((ROOT / "data/expectations.json").read_text())
    for scenario, expected in expectations.items():
        task = create(client, expected["patient_id"], scenario)
        result = task["validation"]
        assert result["status"] == expected["status"]
        assert result["errors"] == expected["errors"]
        assert len(result["medication_changes"]) == expected["medication_change_count"]
        assert not result["submitted"]
        assert task["drafts"] == []
        assert "context_snapshot" not in task
    assert repo.store.dataset == dataset
    assert create(client, "SYN-P002")["validation"]["errors"] == []


@pytest.mark.parametrize("pid", ["SYN-P001", "SYN-P002"])
def test_patient_context_and_citations_are_isolated(client, repo, pid):
    task = create(client, pid)
    context = repo.get_task(task["task_id"])["context_snapshot"]
    assert "expectations" not in json.dumps(context)
    for key in ("documents", "encounters", "medication_snapshots", "requests", "pharmacy_status"):
        assert all(r["patient_id"] == pid for r in context[key])
    assert [p["provider_id"] for p in context["providers"]] == ["DEMO-PROVIDER-001"]
    for ids in task["validation"]["field_sources"].values():
        for rid in ids:
            source = client.get(f'/api/tasks/{task["task_id"]}/sources/{rid}')
            assert source.status_code == 200
            assert source.json().get("patient_id", pid) == pid


def test_known_cross_patient_source_and_contaminated_task_are_rejected(client):
    task = create(client)
    assert client.get(f'/api/tasks/{task["task_id"]}/sources/NOTE-002-2').status_code == 403
    assert client.get(f'/tasks/{task["task_id"]}/sources/NOTE-002-2').status_code == 403
    blocked = create(client, scenario="wrong_patient")
    assert blocked["validation"]["comparison"] is None
    assert blocked["source_record_ids"] == []
    assert blocked["encounters"] == []
    assert client.get(f'/api/tasks/{blocked["task_id"]}/sources/NOTE-002-2').status_code == 403


def test_expected_medications_dates_and_sources(client):
    cmp = create(client)["validation"]["comparison"]
    assert cmp["label"] == "Synthetic record comparison"
    assert cmp["compared_dates"] == ["2026-09-30T14:00:00Z", "2026-10-14T14:00:00Z"]
    change, = cmp["changes"]
    assert (change["medication"], change["before"]["dose"], change["after"]["dose"]) == ("lisinopril", "10 mg", "20 mg")
    assert change["source_record_ids"] == ["MED-001-1", "MED-001-2"]
    assert cmp["unchanged"][0]["after"] == {"name": "metformin", "dose": "500 mg", "frequency": "twice daily"}
    morgan = create(client, "SYN-P002")["validation"]["comparison"]
    assert morgan["changes"] == []
    assert morgan["unchanged"][0]["after"]["dose"] == "5 mg"
    assert "fills are unknown" in cmp["limitation"]


@pytest.mark.parametrize("scenario,pid", [("missing_referral", "SYN-P002"), ("conflicting_medication", "SYN-P001"), ("wrong_patient", "SYN-P001"), ("missing_member_id", "SYN-P001")])
def test_failed_validation_prevents_any_draft_write(client, repo, scenario, pid):
    task = create(client, pid, scenario)
    valid = create(client, pid)
    body = payload(task)
    body["values"] = valid["validation"]["form_fields"]
    response = client.post(f'/mock-payer/requests/{task["task_id"]}/draft', json=body)
    assert response.status_code == 409
    assert response.json()["error"] == "VALIDATION_BLOCKED"
    assert repo.get_drafts(task["task_id"]) == []
    assert len(repo.get_events(task["task_id"])) == 2


def test_valid_draft_and_history_recovered_by_new_app(client, repo):
    task = create(client)
    url = f'/mock-payer/requests/{task["task_id"]}/draft'
    first = client.post(url, json=payload(task))
    assert first.status_code == 201
    draft = first.json()
    assert draft["values"] == task["validation"]["form_fields"]
    assert draft["field_sources"] == task["validation"]["field_sources"]
    assert draft["revision"] == 1 and not draft["submitted"]
    with TestClient(create_app(MemoryRepository(repo.store), Settings())) as restored:
        recovered = restored.get(f'/api/tasks/{task["task_id"]}').json()
        assert recovered["status"] == "draft_saved" and recovered["drafts"] == [draft]
        assert len(recovered["events"]) == 3
        assert restored.post(url, json=payload(task)).status_code == 409
        second = restored.post(url, json=payload(recovered))
        assert second.status_code == 201
        history = restored.get(f'/api/tasks/{task["task_id"]}').json()
        assert len(history["drafts"]) == 2 and len(history["events"]) == 4
        assert history["drafts"][0] == draft
    assert len(client.get("/api/tasks").json()) == 1


@pytest.mark.parametrize("mutation", ["patient", "task", "value", "unknown_field", "context", "units_type"])
def test_payer_substitution_and_unsupported_changes(client, repo, mutation):
    task = create(client)
    body = payload(task)
    expected = 409
    if mutation == "patient":
        body["patient_id"] = "SYN-P002"
        expected = 403
    elif mutation == "task":
        body["task_id"] = "another-task"
        expected = 403
    elif mutation == "value":
        body["values"]["insurance_member_id"] = "other"
    elif mutation == "context":
        body["context_hash"] = "stale"
    elif mutation == "units_type":
        body["values"]["requested_units"] = True
        expected = 422
    elif mutation == "unknown_field":
        body["values"]["approved"] = True
        expected = 422
    assert client.post(f'/mock-payer/requests/{task["task_id"]}/draft', json=body).status_code == expected
    assert repo.get_drafts(task["task_id"]) == []


@pytest.mark.parametrize("path", ["/mock-payer/requests/{id}/submit", "/api/tasks/{id}/submit", "/api/tasks/{id}/approval"])
def test_submission_and_approval_always_rejected(client, path):
    task = create(client)
    client.post(f'/mock-payer/requests/{task["task_id"]}/draft', json=payload(task))
    assert client.post(path.format(id=task["task_id"]), json={"approved": True}).status_code == 403


def test_scenarios_disabled_on_server_and_in_ui(repo):
    with TestClient(create_app(repo, Settings(demo_mode=False))) as client:
        assert 'id="scenario"' not in client.get("/").text
        assert create(client)["scenario"] == "baseline"
        assert client.post("/api/tasks", json={"patient_id": "SYN-P001", "scenario": "happy"}).status_code == 400


def test_scenario_cannot_switch_patient(client):
    assert client.post("/api/tasks", json={"patient_id": "SYN-P001", "scenario": "missing_referral"}).status_code == 400
    assert client.post("/api/tasks", json={"patient_id": "SYN-P999"}).status_code == 404


def test_directory_and_history_only_expose_summaries(client):
    create(client)
    create(client, "SYN-P002")
    for patient in client.get("/api/patients").json():
        assert set(patient) == {"patient_id", "name"}
    for summary in client.get("/api/tasks").json():
        assert "validation" not in summary and "context_snapshot" not in summary
    assert len(client.get("/api/tasks?patient_id=SYN-P001").json()) == 1


@pytest.mark.parametrize("mutation,code", [
    ("negative_units", "INVALID_REQUESTED_UNITS"), ("bool_units", "INVALID_REQUESTED_UNITS"),
    ("bad_date", "INVALID_PLANNED_SERVICE_DATE"), ("bad_dob", "INVALID_DATE_OF_BIRTH"),
    ("bad_timestamp", "INVALID_SOURCE_DATE"), ("unsigned", "MISSING_SIGNED_NOTE"),
    ("string_signature", "INVALID_SIGNATURE_TYPE"), ("encounter", "MISSING_ENCOUNTER"),
    ("med_source", "INVALID_MEDICATION_SOURCE"), ("service", "REFERRAL_SERVICE_MISMATCH"),
    ("provider", "MISSING_PROVIDER"), ("future", "FUTURE_SOURCE_RECORD"),
])
def test_administrative_type_date_and_relationship_checks(dataset, mutation, code):
    bundle = patient_bundle(dataset, "SYN-P001")
    req = bundle["requests"][0]
    note = next(d for d in bundle["documents"] if d["record_id"] == "NOTE-001-2")
    if mutation == "negative_units": req["requested_units"] = -1
    elif mutation == "bool_units": req["requested_units"] = True
    elif mutation == "bad_date": req["planned_service_date"] = "2026-02-31"
    elif mutation == "bad_dob": bundle["patient"]["date_of_birth"] = 123
    elif mutation == "bad_timestamp": note["recorded_at"] = "invalid"
    elif mutation == "unsigned": note["signed"] = False
    elif mutation == "string_signature": note["signed"] = "true"
    elif mutation == "encounter": req["encounter_id"] = "ENC-002-2"
    elif mutation == "med_source": bundle["medication_snapshots"][-1]["source_record_id"] = "REF-001"
    elif mutation == "service": req["service_code"] = "DEMO-OTHER"
    elif mutation == "provider": bundle["providers"] = []
    elif mutation == "future": note["recorded_at"] = "2026-12-01T00:00:00Z"
    result = evaluate(bundle, "SYN-P001")
    assert code in result["errors"]
    assert result["form_fields"] == {} and result["status"] == "blocked"


def test_duplicate_same_time_reconciliation_does_not_replace_previous_date(dataset):
    bundle = patient_bundle(dataset, "SYN-P001")
    duplicate = copy.deepcopy(bundle["medication_snapshots"][-1])
    duplicate["record_id"] = "MED-SAME"
    duplicate["recorded_at"] = "2026-10-15T00:00:00Z"
    bundle["medication_snapshots"].append(duplicate)
    cmp = evaluate(bundle, "SYN-P001")["comparison"]
    assert len(cmp["changes"]) == 1
    assert cmp["compared_dates"][0] == "2026-09-30T14:00:00Z"
    assert "MED-SAME" in cmp["changes"][0]["source_record_ids"]


def test_same_instant_with_different_offsets_still_conflicts(dataset):
    bundle = patient_bundle(dataset, "SYN-P001")
    duplicate = copy.deepcopy(bundle["medication_snapshots"][-1])
    duplicate.update(record_id="MED-CONFLICT", effective_at="2026-10-14T10:00:00-04:00")
    duplicate["medications"][0]["dose"] = "5 mg"
    bundle["medication_snapshots"].append(duplicate)
    assert "CONFLICTING_MEDICATION_RECORDS" in evaluate(bundle, "SYN-P001")["errors"]


def test_missing_complete_reconciliations_blocks(dataset):
    bundle = patient_bundle(dataset, "SYN-P001")
    bundle["medication_snapshots"][-1]["complete_reconciliation"] = False
    assert "INSUFFICIENT_MEDICATION_RECORDS" in evaluate(bundle, "SYN-P001")["errors"]


def test_escaped_source_text_and_mock_portal_selectors(client, repo):
    repo.store.dataset["documents"][0]["text"] = '<script>alert("fixture")</script>'
    task = create(client)
    source = client.get(f'/tasks/{task["task_id"]}/sources/NOTE-001-1').text
    assert '<script>alert("fixture")</script>' not in source
    assert "&lt;script&gt;" in source
    portal = client.get(f'/mock-payer/requests/{task["task_id"]}').text
    for selector in ("save-draft", "draft-status", "mock-receipt"):
        assert f'data-testid="{selector}"' in portal
    for field in task["validation"]["form_fields"]:
        assert f'name="{field}"' in portal
    assert "Synthetic demo" in client.get("/").text


def test_storage_failures_visible_and_never_fall_back(repo, monkeypatch):
    monkeypatch.setattr(repo, "list_patients", lambda: (_ for _ in ()).throw(StorageUnavailable("test outage")))
    with TestClient(create_app(repo, Settings())) as client:
        assert client.get("/api/patients").status_code == 503
        assert "storage unavailable" in client.get("/").text
    assert not repo.store.tasks


def test_failed_write_does_not_report_a_saved_draft(client, repo, monkeypatch):
    task = create(client)
    monkeypatch.setattr(repo, "save_draft", lambda *args: (_ for _ in ()).throw(StorageUnavailable("write failed")))
    assert client.post(f'/mock-payer/requests/{task["task_id"]}/draft', json=payload(task)).status_code == 503
    assert repo.get_task(task["task_id"])["draft_revision"] == 0
    assert repo.get_drafts(task["task_id"]) == []


def test_concurrent_saves_have_one_winner(client, repo):
    task = create(client)
    def save(_):
        with TestClient(create_app(MemoryRepository(repo.store), Settings())) as other:
            return other.post(f'/mock-payer/requests/{task["task_id"]}/draft', json=payload(task)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [201, 409]
    assert len(repo.get_drafts(task["task_id"])) == 1
    assert len(repo.get_events(task["task_id"])) == 3
