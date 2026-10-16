"""Explicitly injected test storage. Production has no in-memory fallback."""
import copy
from threading import RLock

from core import patient_bundle
from app.repository import ConcurrentWrite


class Store:
    def __init__(self, dataset):
        self.dataset = copy.deepcopy(dataset)
        self.tasks, self.drafts, self.events = {}, {}, {}
        self.lock = RLock()


class MemoryRepository:
    def __init__(self, store):
        self.store = store

    def list_patients(self):
        return [{"patient_id": p["patient_id"], "name": p["name"]} for p in self.store.dataset["patients"]]

    def load_patient_context(self, patient_id):
        try:
            return patient_bundle(self.store.dataset, patient_id)
        except ValueError as exc:
            raise KeyError(patient_id) from exc

    def create_task(self, task, events):
        with self.store.lock:
            self.store.tasks[task["task_id"]] = copy.deepcopy(task)
            self.store.events[task["task_id"]] = copy.deepcopy(events)

    def get_task(self, task_id):
        return copy.deepcopy(self.store.tasks[task_id])

    def list_tasks(self, patient_id=None):
        fields = {"task_id", "patient_id", "scenario", "status", "draft_revision", "created_at", "updated_at"}
        return [copy.deepcopy({k: v for k, v in t.items() if k in fields})
                for t in self.store.tasks.values() if patient_id is None or t["patient_id"] == patient_id]

    def get_drafts(self, task_id):
        return copy.deepcopy(self.store.drafts.get(task_id, []))

    def get_events(self, task_id):
        return copy.deepcopy(self.store.events.get(task_id, []))

    def save_draft(self, task, draft, event, expected_revision):
        with self.store.lock:
            current = self.store.tasks[task["task_id"]]
            if current["draft_revision"] != expected_revision or current["context_hash"] != task["context_hash"]:
                raise ConcurrentWrite()
            self.store.tasks[task["task_id"]] = copy.deepcopy(task)
            self.store.drafts.setdefault(task["task_id"], []).append(copy.deepcopy(draft))
            self.store.events[task["task_id"]].append(copy.deepcopy(event))
