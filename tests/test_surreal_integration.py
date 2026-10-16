"""Real HTTP checks; skipped unless explicitly enabled. Never uses test memory storage."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.main import create_app
from app.repository import SurrealRepository
from .conftest import create, payload

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("CAREFLOW_RUN_DB_TESTS") != "1", reason="Set CAREFLOW_RUN_DB_TESTS=1 for a real local SurrealDB check")]


@pytest.fixture
def database():
    settings = replace(Settings.from_env(), database="pr1_test_" + uuid4().hex)
    repo = SurrealRepository(settings)
    data = json.loads((ROOT / "data/dataset.json").read_text())
    repo.seed((ROOT / "data/seed.surql").read_text(), data["snapshot_at"])
    yield settings, repo
    # Database identifiers cannot be query values. This name is solely a fixed prefix
    # and internally generated UUID, never request input or an environment value.
    repo.client.query(f"REMOVE DATABASE {settings.database};")


def test_real_seed_draft_restart_recovery_and_scenarios(database):
    settings, repo = database
    with TestClient(create_app(repo, settings)) as client:
        assert len(client.get("/api/patients").json()) == 2
        happy = create(client)
        assert happy["validation"]["errors"] == []
        task_id = happy["task_id"]
        draft = client.post(f"/mock-payer/requests/{task_id}/draft", json=payload(happy))
        assert draft.status_code == 201, draft.text
        for scenario, expected in json.loads((ROOT / "data/expectations.json").read_text()).items():
            task = create(client, expected["patient_id"], scenario)
            assert task["validation"]["errors"] == expected["errors"]
            if expected["status"] == "blocked":
                body = payload(task)
                body["values"] = create(client, expected["patient_id"])["validation"]["form_fields"]
                assert client.post(f'/mock-payer/requests/{task["task_id"]}/draft', json=body).status_code == 409
                assert repo.get_drafts(task["task_id"]) == []
        assert client.get(f"/api/tasks/{task_id}/sources/NOTE-002-2").status_code == 403
    # A fresh repository/client/app reads persisted values via a new HTTP connection.
    fresh = SurrealRepository(settings)
    fresh.seed((ROOT / "data/seed.surql").read_text(), "2026-10-16T16:00:00Z")
    with TestClient(create_app(fresh, settings)) as restarted:
        recovered = restarted.get(f"/api/tasks/{task_id}").json()
        assert recovered["drafts"] == [draft.json()]
        assert recovered["status"] == "draft_saved" and recovered["draft_revision"] == 1
        assert len(recovered["events"]) == 3
        assert restarted.get("/").status_code == 200
        assert restarted.get(f"/tasks/{task_id}").status_code == 200
        assert restarted.get(f"/mock-payer/requests/{task_id}").status_code == 200
        assert restarted.post(f"/mock-payer/requests/{task_id}/submit").status_code == 403
        assert len(fresh.list_tasks()) >= 7


def test_real_atomic_compare_and_swap(database):
    settings, repo = database
    with TestClient(create_app(repo, settings)) as client:
        task = create(client)
    def save(_):
        with TestClient(create_app(SurrealRepository(settings), settings)) as client:
            response = client.post(f'/mock-payer/requests/{task["task_id"]}/draft', json=payload(task))
            return response.status_code, response.text
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sorted(code for code, _ in results) == [201, 409], results
    assert len(repo.get_drafts(task["task_id"])) == 1
    assert len(repo.get_events(task["task_id"])) == 3
