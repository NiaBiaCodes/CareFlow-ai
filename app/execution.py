"""Durable orchestration. Provider failures never become reference answers."""
from uuid import uuid4
from threading import Lock

from worker.contracts import assert_patient, validate_answer
from worker.telemetry import Telemetry

from .daytona_adapter import DaytonaAdapter
from .workflow import WorkflowError, digest, now
from .portal_security import PortalTokens
from .cleanup import cleanup_execution, recover


class LiveExecution:
    def __init__(self, workflow, adapter_factory=DaytonaAdapter):
        self.workflow = workflow
        self.settings = workflow.settings
        self.adapter_factory = adapter_factory
        self.active, self.lock = set(), Lock()

    def run(self, task_id):
        with self.lock:
            if task_id in self.active: return
            self.active.add(task_id)
        telemetry = Telemetry(task_id, self.settings.phoenix_endpoint, self.settings.phoenix_key)
        try:
            with telemetry.span("careflow.execution"):
                self._run(task_id, telemetry)
        finally:
            try:
                report = telemetry.finish()
                self.workflow.change(task_id, "telemetry_result", lambda t: t.update(telemetry=report))
            finally:
                with self.lock: self.active.discard(task_id)

    def _run(self, task_id, telemetry):
        task = self.workflow.repo.get_task(task_id)
        if task.get("execution_mode") != "live" or task["status"] != "created":
            return
        missing = self.settings.missing_live()
        if missing:
            self.workflow.change(task_id, "configuration_missing", lambda t: t.update(
                status="configuration_missing", configuration_missing=missing))
            return
        with telemetry.span("context.retrieval", **{"careflow.correct_patient": True}):
            assert_patient(task["context_snapshot"], task["patient_id"])
        if digest(task["context_snapshot"]) != task["context_hash"]:
            raise WorkflowError("CONTEXT_REVISION_MISMATCH")
        operation_id = str(uuid4())
        def begin(t):
            if t["status"] != "created":
                raise WorkflowError("EXECUTION_ALREADY_STARTED")
            t["status"] = "running"
            t["executions"].append({"execution_id": operation_id, "operation": "draft",
                "started_at": now(), "sandbox_id": None, "sandbox_cleanup": "not_created", "browser_sessions": []})
        self.workflow.change(task_id, "execution_started", begin)
        adapter, sandbox = None, None
        try:
            adapter = self.adapter_factory(self.settings)
            with telemetry.span("sandbox.create"):
                sandbox = adapter.create(task_id)
            self.resource(task_id, operation_id, sandbox_id=sandbox.id, sandbox_cleanup="pending")
            adapter.prepare(sandbox)
            with telemetry.span("sandbox.model_execution"):
                result = adapter.execute(sandbox, {"task_id": task_id, "patient_id": task["patient_id"],
                    "execution_id": operation_id, "operation": "answer", "question": task["question"],
                    "trace_context": telemetry.carrier(),
                    "context": task["context_snapshot"], "context_hash": task["context_hash"]})
            with telemetry.span("answer.validation") as span:
                answer = validate_answer(result["answer"], task["context_snapshot"], task["patient_id"])
                span.set_attribute("careflow.supported_facts_and_citations", True)
                span.set_attribute("careflow.required_documents_present", not bool(task["validation"]["errors"]))
                span.set_attribute("careflow.validation_result", "blocked" if task["validation"]["errors"] else "valid")
            if result.get("telemetry"):
                self.resource(task_id, operation_id, worker_telemetry=result["telemetry"])
            self.workflow.change(task_id, "model_validated", lambda t: t.update(
                live_answer={"label": "Live model result · validated against source records", "answer": answer,
                             "model": result["model"], "response_id": result["response_id"]},
                status="blocked" if t["validation"]["errors"] else "ready_for_review"))
            if not task["validation"]["errors"]:
                self.create_browser_draft(task_id, operation_id, adapter, sandbox, telemetry)
        except Exception as exc:
            telemetry.error(exc)
            if getattr(exc, "telemetry", None):
                self.resource(task_id, operation_id, failed_worker_telemetry=exc.telemetry)
            # A timeout after a browser click is ambiguous: consult durable portal
            # state before allowing a retry. Never create a second revision blindly.
            drafts = self.workflow.repo.get_drafts(task_id)
            recovered = bool(drafts and drafts[-1].get("browser_verified"))
            self.workflow.change(task_id, "execution_error", lambda t: t.update(
                status="draft_saved" if recovered else "failed", execution_error=getattr(exc, "code", type(exc).__name__),
                acknowledgment_recovered=recovered))
        finally:
            if sandbox is not None:
                cleanup_execution(self, task_id, operation_id, adapter, sandbox, telemetry)

    def create_browser_draft(self, task_id, operation_id, adapter, sandbox, telemetry):
        missing = self.settings.missing_live(browser=True)
        if missing:
            self.workflow.change(task_id, "browser_configuration_missing", lambda t: t.update(
                status="configuration_missing", configuration_missing=missing))
            return
        task = self.workflow.repo.get_task(task_id)
        token = PortalTokens(self.settings.portal_signing_key).mint(task_id, operation_id, "draft")
        with telemetry.span("browser.draft", **{"careflow.required_documents_present": True}) as span:
            result = adapter.execute(sandbox, {"task_id": task_id, "patient_id": task["patient_id"],
                "execution_id": operation_id, "operation": "draft", "context": task["context_snapshot"],
                "payer_url": self.settings.payer_url, "portal_token": token,
                "values": task["validation"]["form_fields"], "trace_context": telemetry.carrier()})
            drafts = self.workflow.repo.get_drafts(task_id)
            if (not drafts or not drafts[-1].get("browser_verified") or
                    drafts[-1]["values"] != result["verified_values"]):
                raise WorkflowError("PORTAL_PERSISTENCE_MISMATCH")
            span.set_attribute("careflow.exact_form_values", True)
            self.resource(task_id, operation_id, draft_id=drafts[-1]["draft_id"],
                verified_values=result["verified_values"], browser_telemetry=result.get("telemetry", {}))

    def reconcile_browser(self, task_id, operation_id, journal):
        def update(task):
            execution = next(e for e in task["executions"] if e["execution_id"] == operation_id)
            sessions = execution["browser_sessions"]
            session = next((s for s in sessions if s["session_id"] == journal["session_id"]), None)
            if session is None:
                session = {"session_id": journal["session_id"], "recording_reference": "anchor-session:" + journal["session_id"]}
                sessions.append(session)
            session["cleanup"] = journal["cleanup"]
        self.workflow.change(task_id, "browser_journal_recovered", update)

    def resource(self, task_id, operation_id, **values):
        def update(t):
            execution = next(e for e in t["executions"] if e["execution_id"] == operation_id)
            execution.update(values)
        return self.workflow.change(task_id, "execution_resource", update)

    def submit(self, task_id):
        with self.lock:
            if task_id in self.active: return
            self.active.add(task_id)
        try: self._submit(task_id)
        finally:
            with self.lock: self.active.discard(task_id)

    def _submit(self, task_id):
        telemetry = Telemetry(task_id, self.settings.phoenix_endpoint, self.settings.phoenix_key)
        sandbox, adapter, operation_id = None, None, str(uuid4())
        started = False
        try:
            with telemetry.span("careflow.approved_submission"):
                task = self.workflow.repo.get_task(task_id)
                if task.get("receipt"): return  # Retry consults the persisted portal state first.
                binding = self.workflow.approved_binding(task)
                self.workflow.require_cleaned(task)
                missing = self.settings.missing_live(browser=True)
                if missing: raise WorkflowError("SUBMISSION_CONFIGURATION_MISSING")
                def begin(t):
                    if t["status"] != "approved" or self.workflow.approved_binding(t) != binding:
                        raise WorkflowError("APPROVED_DRAFT_CHANGED")
                    t["status"] = "submission_running"
                    t["executions"].append({"execution_id": operation_id, "operation": "submit",
                        "approval_binding_hash": digest(binding), "started_at": now(), "sandbox_id": None,
                        "sandbox_cleanup": "not_created", "browser_sessions": []})
                self.workflow.change(task_id, "submission_execution_started", begin)
                started = True
                adapter = self.adapter_factory(self.settings)
                with telemetry.span("sandbox.create_for_submission"):
                    sandbox = adapter.create(task_id, purpose="submit")
                self.resource(task_id, operation_id, sandbox_id=sandbox.id, sandbox_cleanup="pending")
                adapter.prepare(sandbox)
                token = PortalTokens(self.settings.portal_signing_key).mint(task_id, operation_id, "submit")
                with telemetry.span("browser.submission", **{"careflow.approval_enforced": True}) as span:
                    result = adapter.execute(sandbox, {"operation": "submit", "task_id": task_id,
                        "patient_id": task["patient_id"], "execution_id": operation_id,
                        "context": task["context_snapshot"], "payer_url": self.settings.payer_url,
                        "portal_token": token, "values": task["validation"]["form_fields"],
                        "draft_hash": binding["draft_hash"], "draft_revision": binding["draft_revision"],
                        "trace_context": telemetry.carrier()})
                    saved = self.workflow.repo.get_task(task_id)
                    if not saved.get("receipt") or saved["receipt"]["receipt_id"] != result["receipt_id"]:
                        raise WorkflowError("MOCK_RECEIPT_NOT_PERSISTED")
                    span.set_attribute("careflow.single_submission_receipt", True)
                    span.set_attribute("careflow.exact_form_values", result["verified_values"] == task["validation"]["form_fields"])
                    self.resource(task_id, operation_id, receipt_id=result["receipt_id"],
                        worker_telemetry=result.get("telemetry", {}))
        except Exception as exc:
            telemetry.error(exc)
            if not started: return
            def failed(t):
                t["submission_error"] = type(exc).__name__
                if t.get("receipt"):
                    t["status"], t["acknowledgment_recovered"] = "submitted_to_mock", True
                elif t["status"] == "submission_running": t["status"] = "approved"
            self.workflow.change(task_id, "submission_execution_error", failed)
        finally:
            if sandbox is not None:
                cleanup_execution(self, task_id, operation_id, adapter, sandbox, telemetry)
            report = telemetry.finish()
            self.workflow.change(task_id, "submission_telemetry_result", lambda t: t.update(submission_telemetry=report))

    def recover(self, task_id):
        try: return recover(self, task_id)
        except WorkflowError: raise
        except Exception as exc:
            self.workflow.change(task_id, "cleanup_retry_error", lambda t: t.update(cleanup_error=type(exc).__name__))
            return self.workflow.view(task_id)
