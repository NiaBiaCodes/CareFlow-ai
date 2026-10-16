import json

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.main import create_app
from .support import MemoryRepository, Store


@pytest.fixture
def dataset():
    return json.loads((ROOT / "data/dataset.json").read_text())


@pytest.fixture
def repo(dataset):
    return MemoryRepository(Store(dataset))


@pytest.fixture
def client(repo):
    with TestClient(create_app(repo, Settings())) as client:
        yield client


def create(client, patient="SYN-P001", scenario=None):
    response = client.post("/api/tasks", json={"patient_id": patient, "scenario": scenario})
    assert response.status_code == 201, response.text
    return response.json()


def payload(task):
    return {"task_id": task["task_id"], "patient_id": task["patient_id"],
            "context_hash": task["context_hash"], "expected_revision": task["draft_revision"],
            "values": task["validation"]["form_fields"]}
