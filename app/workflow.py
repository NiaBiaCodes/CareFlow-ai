"""Task lifecycle and evidence-bound, append-only draft revisions."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from .context import permitted_sources, select_context
from .repository import ConcurrentWrite
from .validation import evaluate


class WorkflowError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def event(task, kind, sequence, details=None):
    return {"event_id": str(uuid4()), "task_id": task["task_id"], "patient_id": task["patient_id"],
            "kind": kind, "sequence": sequence, "created_at": now(), "details": details or {}}


class Workflow:
    def __init__(self, repository, settings):
        self.repo, self.settings = repository, settings

    def create(self, request):
        try:
            bundle = select_context(self.repo, request.patient_id, request.scenario, self.settings.demo_mode)
        except ValueError as exc:
            raise WorkflowError(str(exc), 400) from exc
        result = evaluate(bundle, request.patient_id)
        task = {"task_id": str(uuid4()), "patient_id": request.patient_id,
                "request_id": bundle["requests"][0]["request_id"] if len(bundle["requests"]) == 1 else None,
                "scenario": request.scenario or "baseline", "question": request.question,
                "task_type": request.task_type, "context_snapshot": bundle,
                "context_revision": digest(bundle), "context_hash": digest(bundle),
                "source_record_ids": sorted(permitted_sources(bundle, request.patient_id)),
                "validation": result, "status": result["status"], "draft_revision": 0,
                "draft_hash": None, "created_at": now(), "updated_at": now()}
        self.repo.create_task(task, [event(task, "created", 1), event(task, result["status"], 2, {"errors": result["errors"]})])
        return self.view(task["task_id"])

    def view(self, task_id):
        task = self.repo.get_task(task_id)
        context = task.pop("context_snapshot")
        allowed = permitted_sources(context, task["patient_id"])
        task["patient"] = context["patient"]
        task["encounters"] = context["encounters"] if allowed else []
        task["medication_snapshots"] = context["medication_snapshots"] if allowed else []
        task["source_record_ids"] = sorted(allowed)
        task["drafts"] = self.repo.get_drafts(task_id)
        task["events"] = self.repo.get_events(task_id)
        return task

    def source(self, task_id, record_id):
        task = self.repo.get_task(task_id)
        allowed = permitted_sources(task["context_snapshot"], task["patient_id"])
        if record_id not in allowed or record_id not in task["source_record_ids"]:
            raise WorkflowError("SOURCE_NOT_PERMITTED", 403)
        return allowed[record_id]

    def save(self, task_id, request):
        task = self.repo.get_task(task_id)
        if request.task_id != task_id or request.patient_id != task["patient_id"]:
            raise WorkflowError("TASK_PATIENT_SUBSTITUTION", 403)
        if request.values.patient_id != task["patient_id"]:
            raise WorkflowError("TASK_PATIENT_SUBSTITUTION", 403)
        if request.context_hash != task["context_hash"] or digest(task["context_snapshot"]) != task["context_hash"]:
            raise WorkflowError("CONTEXT_REVISION_MISMATCH")
        # Never trust browser fields or a previously stored validation flag.
        result = evaluate(task["context_snapshot"], task["patient_id"])
        if result["errors"] or task["status"] not in {"ready_for_review", "draft_saved"}:
            raise WorkflowError("VALIDATION_BLOCKED")
        if request.values.model_dump() != result["form_fields"]:
            raise WorkflowError("UNSUPPORTED_FIELD_CHANGE")
        if request.expected_revision != task["draft_revision"]:
            raise WorkflowError("DRAFT_REVISION_MISMATCH")
        revision = task["draft_revision"] + 1
        draft = {"draft_id": str(uuid4()), "task_id": task_id, "patient_id": task["patient_id"],
                 "revision": revision, "context_hash": task["context_hash"],
                 "values": copy.deepcopy(result["form_fields"]), "field_sources": result["field_sources"],
                 "validation": result, "created_at": now(), "submitted": False}
        draft["draft_hash"] = digest(draft)
        task.update(draft_revision=revision, draft_hash=draft["draft_hash"], status="draft_saved", updated_at=now())
        try:
            self.repo.save_draft(task, draft, event(task, "draft_saved", revision + 2,
                                 {"draft_id": draft["draft_id"], "revision": revision}), request.expected_revision)
        except ConcurrentWrite as exc:
            raise WorkflowError("DRAFT_REVISION_MISMATCH") from exc
        return draft
