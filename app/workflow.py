"""Task lifecycle and evidence-bound, append-only draft revisions."""
import copy
import hashlib
import json
import secrets
import hmac
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
                "execution_mode": request.execution_mode, "version": 0, "event_sequence": 2,
                "executions": [], "live_answer": None,
                "request_id": bundle["requests"][0]["request_id"] if len(bundle["requests"]) == 1 else None,
                "scenario": request.scenario or "baseline", "question": request.question,
                "task_type": request.task_type, "context_snapshot": bundle,
                "context_revision": digest(bundle), "context_hash": digest(bundle),
                "source_record_ids": sorted(permitted_sources(bundle, request.patient_id)),
                "validation": result, "status": result["status"], "draft_revision": 0,
                "draft_hash": None, "created_at": now(), "updated_at": now()}
        if request.execution_mode == "live" and not any(e not in {"MISSING_REFERRAL", "MISSING_MEMBER_ID"} for e in result["errors"]):
            task["status"] = "created"
        self.repo.create_task(task, [event(task, "created", 1), event(task, result["status"], 2, {"errors": result["errors"]})])
        return self.view(task["task_id"])

    def change(self, task_id, kind, mutate, details=None):
        for _ in range(5):
            task = self.repo.get_task(task_id)
            version = task.get("version", 0)
            mutate(task)
            task.update(version=version + 1, event_sequence=task.get("event_sequence", 2) + 1, updated_at=now())
            try:
                self.repo.update_task(task, event(task, kind, task["event_sequence"], details), version)
                return task
            except ConcurrentWrite:
                continue
        raise WorkflowError("TASK_BUSY")

    def view(self, task_id):
        task = self.repo.get_task(task_id)
        context = task.pop("context_snapshot")
        task.pop("review_nonce_hash", None)
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

    def save(self, task_id, request, portal_access=None):
        task = self.repo.get_task(task_id)
        if request.task_id != task_id or request.patient_id != task["patient_id"]:
            raise WorkflowError("TASK_PATIENT_SUBSTITUTION", 403)
        if request.values.patient_id != task["patient_id"]:
            raise WorkflowError("TASK_PATIENT_SUBSTITUTION", 403)
        if request.context_hash != task["context_hash"] or digest(task["context_snapshot"]) != task["context_hash"]:
            raise WorkflowError("CONTEXT_REVISION_MISMATCH")
        # Never trust browser fields or a previously stored validation flag.
        result = evaluate(task["context_snapshot"], task["patient_id"])
        if result["errors"] or task["status"] not in {"ready_for_review", "draft_saved", "approved"}:
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
        if portal_access:
            execution = next((e for e in task["executions"] if e["execution_id"] == portal_access["execution_id"]), None)
            if not execution or execution.get("verified_values_hash") != digest(result["form_fields"]):
                raise WorkflowError("BROWSER_READBACK_REQUIRED")
            draft["browser_verified"] = True
            draft["browser_session_id"] = execution["verified_session_id"]
            # Include verified provenance in the exact draft identity.
            draft.pop("draft_hash")
            draft["draft_hash"] = digest(draft)
        task.update(draft_revision=revision, draft_hash=draft["draft_hash"], status="draft_saved", updated_at=now(),
                    version=task.get("version", 0)+1, event_sequence=task.get("event_sequence", 2)+1,
                    approval=None, review_nonce_hash=None)
        try:
            self.repo.save_draft(task, draft, event(task, "draft_saved", task["event_sequence"],
                                 {"draft_id": draft["draft_id"], "revision": revision}), request.expected_revision)
        except ConcurrentWrite as exc:
            raise WorkflowError("DRAFT_REVISION_MISMATCH") from exc
        return draft

    def browser_event(self, task_id, access, body):
        def update(task):
            execution = next((e for e in task["executions"] if e["execution_id"] == access["execution_id"]), None)
            if not execution or execution["sandbox_cleanup"] == "deleted":
                raise WorkflowError("EXECUTION_NOT_ACTIVE", 403)
            sessions = execution["browser_sessions"]
            session = next((s for s in sessions if s["session_id"] == body.session_id), None)
            if body.kind == "browser_created":
                if not session: sessions.append({"session_id": body.session_id, "cleanup": "pending",
                    "recording_reference": "anchor-session:" + body.session_id})
            elif session is None:
                raise WorkflowError("UNKNOWN_BROWSER_SESSION", 403)
            elif body.kind == "fields_verified":
                if body.values is None or body.values.model_dump() != task["validation"]["form_fields"]:
                    raise WorkflowError("BROWSER_FIELD_MISMATCH")
                execution.update(verified_values_hash=digest(body.values.model_dump()), verified_session_id=body.session_id)
            else:
                session["cleanup"] = "ended" if body.kind == "browser_closed" else "cleanup_pending"
        self.change(task_id, body.kind, update)
        return {"persisted": True}

    def ensure_current_context(self, task):
        current = select_context(self.repo, task["patient_id"],
            None if task["scenario"] == "baseline" else task["scenario"], self.settings.demo_mode)
        if digest(current) != task["context_hash"]:
            self.change(task["task_id"], "approval_invalidated", lambda t: t.update(
                status="stale_context", approval=None, review_nonce_hash=None))
            raise WorkflowError("SOURCE_RECORDS_CHANGED_REVIEW_AGAIN")

    def binding(self, task):
        if task.get("execution_mode") != "live" or not task.get("live_answer"):
            raise WorkflowError("LIVE_VERIFIED_DRAFT_REQUIRED", 403)
        drafts = self.repo.get_drafts(task["task_id"])
        if not drafts or not drafts[-1].get("browser_verified"):
            raise WorkflowError("BROWSER_VERIFIED_DRAFT_REQUIRED", 403)
        draft = drafts[-1]
        if (draft["revision"] != task["draft_revision"] or draft["draft_hash"] != task["draft_hash"]
                or digest({k: v for k, v in draft.items() if k != "draft_hash"}) != task["draft_hash"]):
            raise WorkflowError("DRAFT_HASH_MISMATCH")
        return {k: task[k] for k in ("task_id", "patient_id", "context_revision", "context_hash", "draft_revision", "draft_hash")}

    def require_cleaned(self, task):
        if any(e["sandbox_cleanup"] not in {"deleted", "not_created_confirmed"} or any(s["cleanup"] != "ended" for s in e["browser_sessions"])
               for e in task["executions"]):
            raise WorkflowError("EXECUTION_CLEANUP_REQUIRED")

    def review(self, task_id):
        task = self.repo.get_task(task_id)
        if task["status"] != "draft_saved": raise WorkflowError("DRAFT_NOT_READY_FOR_APPROVAL")
        self.ensure_current_context(task)
        self.require_cleaned(task)
        binding = self.binding(task)
        nonce = secrets.token_urlsafe(32)
        def update(t):
            if self.binding(t) != binding or t["status"] != "draft_saved": raise WorkflowError("DRAFT_CHANGED")
            t["review_nonce_hash"] = digest(nonce)
        self.change(task_id, "clinician_review_opened", update)
        return {"nonce": nonce, **binding}

    def approve(self, task_id, request):
        task = self.repo.get_task(task_id)
        self.ensure_current_context(task)
        self.require_cleaned(task)
        binding = self.binding(task)
        def update(t):
            if t["status"] != "draft_saved" or self.binding(t) != binding: raise WorkflowError("DRAFT_CHANGED")
            if (request.context_hash != binding["context_hash"] or request.draft_hash != binding["draft_hash"]
                    or request.draft_revision != binding["draft_revision"]): raise WorkflowError("APPROVAL_REVISION_MISMATCH")
            if not hmac.compare_digest(t.get("review_nonce_hash") or "", digest(request.nonce)):
                raise WorkflowError("CLINICIAN_REVIEW_NONCE_REQUIRED", 403)
            t.update(approval={"binding": binding, "binding_hash": digest(binding), "approved_at": now(),
                               "approved_by": "local-demo-clinician"}, status="approved", review_nonce_hash=None)
        approved = self.change(task_id, "clinician_approved", update)
        return approved["approval"]

    def approved_binding(self, task):
        self.ensure_current_context(task)
        binding = self.binding(task)
        if not task.get("approval") or task["approval"]["binding_hash"] != digest(binding):
            raise WorkflowError("CLINICIAN_APPROVAL_REQUIRED", 403)
        return binding

    def submit(self, task_id, request, access):
        if access is None or access["scope"] != "submit": raise WorkflowError("SUBMISSION_CAPABILITY_REQUIRED", 403)
        task = self.repo.get_task(task_id)
        binding = self.approved_binding(task)
        if (request.task_id != task_id or request.patient_id != task["patient_id"] or
                request.context_hash != binding["context_hash"] or request.draft_revision != binding["draft_revision"] or
                request.draft_hash != binding["draft_hash"] or request.values.model_dump() != task["validation"]["form_fields"]):
            raise WorkflowError("APPROVED_PAYLOAD_MISMATCH", 403)
        execution = next((e for e in task["executions"] if e["execution_id"] == access["execution_id"]), None)
        if (not execution or execution.get("approval_binding_hash") != digest(binding)
                or execution.get("verified_values_hash") != digest(request.values.model_dump())):
            raise WorkflowError("APPROVED_BROWSER_READBACK_REQUIRED", 403)
        if task.get("receipt"): return task["receipt"]
        if task["status"] != "submission_running": raise WorkflowError("SUBMISSION_NOT_ACTIVE")
        receipt = {"receipt_id": "SIM-" + digest(binding)[:24], "label": "Simulated receipt",
            "task_id": task_id, "patient_id": task["patient_id"], "draft_revision": task["draft_revision"],
            "draft_hash": task["draft_hash"], "approval_binding_hash": digest(binding),
            "idempotency_key": task_id + ":" + str(task["draft_revision"]), "created_at": now()}
        version = task.get("version", 0)
        task.update(status="submitted_to_mock", receipt=receipt, version=version+1,
                    event_sequence=task["event_sequence"]+1, updated_at=now())
        try:
            self.repo.submit_task(task, receipt, event(task, "submitted_to_mock", task["event_sequence"]), version)
        except ConcurrentWrite:
            recovered = self.repo.get_task(task_id).get("receipt")
            if recovered and recovered["idempotency_key"] == receipt["idempotency_key"]: return recovered
            raise WorkflowError("SUBMISSION_BUSY") from None
        return receipt
