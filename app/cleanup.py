"""Independent cleanup attempts and explicit recovery from durable resource IDs."""
from worker.browser import end_session

from .workflow import WorkflowError, now


def cleanup_execution(service, task_id, operation_id, adapter, sandbox, telemetry):
    journal_error = False
    pending = set()
    try:
        journal = adapter.journal(sandbox) if hasattr(adapter, "journal") else None
        if journal:
            if journal["cleanup"] != "ended": pending.add(journal["session_id"])
            service.reconcile_browser(task_id, operation_id, journal)
    except Exception:
        journal_error = True
    try:
        current = service.workflow.repo.get_task(task_id)
        execution = next(e for e in current["executions"] if e["execution_id"] == operation_id)
        pending.update(s["session_id"] for s in execution["browser_sessions"] if s["cleanup"] != "ended")
    except Exception: journal_error = True
    if pending and service.settings.anchor_key:
        from anchorbrowser import Anchorbrowser
        client = Anchorbrowser(api_key=service.settings.anchor_key, max_retries=0)
        for session_id in pending:
            closed = False
            try:
                with telemetry.span("browser.cleanup") as span:
                    closed = end_session(client, session_id)
                    span.set_attribute("careflow.browser_cleanup", closed)
            except Exception: pass
            try:
                service.reconcile_browser(task_id, operation_id,
                    {"session_id": session_id, "cleanup": "ended" if closed else "cleanup_pending"})
            except Exception: journal_error = True
    # A failed journal download/write must never skip sandbox termination.
    try:
        with telemetry.span("sandbox.cleanup") as span:
            adapter.delete(sandbox)
            span.set_attribute("careflow.resource_cleanup", True)
        status = "deleted"
    except Exception:
        status = "cleanup_pending"
    service.resource(task_id, operation_id, sandbox_cleanup=status, finished_at=now(),
                     journal_recovery_failed=journal_error)


def recover(service, task_id):
    if task_id in service.active: raise WorkflowError("EXECUTION_STILL_RUNNING")
    task = service.workflow.repo.get_task(task_id)
    if task.get("execution_mode") != "live": raise WorkflowError("NO_LIVE_EXECUTION")
    adapter = service.adapter_factory(service.settings)
    found = adapter.find(task_id)  # Handles a create timeout before the ID reached the app.
    known = {e["sandbox_id"] for e in task["executions"] if e.get("sandbox_id")}
    unassigned = [s for s in found if s.id not in known]
    for execution in task["executions"]:
        eid, sid = execution["execution_id"], execution.get("sandbox_id")
        if not sid:
            if unassigned:
                sid = unassigned.pop().id
                service.resource(task_id, eid, sandbox_id=sid, sandbox_cleanup="cleanup_pending")
            else:
                service.resource(task_id, eid, sandbox_cleanup="not_created_confirmed")
                continue
        if execution["sandbox_cleanup"] != "deleted":
            try:
                sandbox = next((s for s in found if s.id == sid), None)
                if sandbox is not None:
                    try:
                        journal = adapter.journal(sandbox)
                        if journal: service.reconcile_browser(task_id, eid, journal)
                    finally: adapter.delete(sandbox)
                service.resource(task_id, eid, sandbox_cleanup="deleted")
            except Exception:
                service.resource(task_id, eid, sandbox_cleanup="cleanup_pending")
    for sandbox in unassigned:
        from uuid import uuid4
        eid = str(uuid4())
        def discovered(t):
            t["executions"].append({"execution_id": eid, "operation": "recovery", "sandbox_id": sandbox.id,
                "sandbox_cleanup": "cleanup_pending", "browser_sessions": [], "started_at": now()})
        service.workflow.change(task_id, "additional_sandbox_discovered", discovered)
        try:
            adapter.delete(sandbox)
            service.resource(task_id, eid, sandbox_cleanup="deleted")
        except Exception:
            service.resource(task_id, eid, sandbox_cleanup="cleanup_pending")
    # Task-tag discovery recovers an Anchor ID even after an interrupted callback.
    current = service.workflow.repo.get_task(task_id)
    if service.settings.anchor_key:
        from anchorbrowser import Anchorbrowser
        anchor = Anchorbrowser(api_key=service.settings.anchor_key, max_retries=0)
        discovered = anchor.sessions.get_all_sessions_status(tags=task_id, timeout=20).data
        known_sessions = {s["session_id"] for e in current["executions"] for s in e["browser_sessions"]}
        for session in (discovered.items or []) if discovered else []:
            if session.session_id not in known_sessions and current["executions"]:
                ended = str(session.status).lower() in {"ended", "terminated", "deleted", "closed", "completed"}
                service.reconcile_browser(task_id, current["executions"][-1]["execution_id"],
                    {"session_id": session.session_id, "cleanup": "ended" if ended else "cleanup_pending"})
        for execution in service.workflow.repo.get_task(task_id)["executions"]:
            for session in execution["browser_sessions"]:
                if session["cleanup"] == "ended": continue
                try: closed = end_session(anchor, session["session_id"])
                except Exception: closed = False
                service.reconcile_browser(task_id, execution["execution_id"],
                    {"session_id": session["session_id"], "cleanup": "ended" if closed else "cleanup_pending"})
    def reconcile(t):
        if t.get("receipt"): t["status"] = "submitted_to_mock"
        elif service.workflow.repo.get_drafts(task_id): t["status"] = "approved" if t.get("approval") else "draft_saved"
        elif t["status"] in {"running", "submission_running"}: t["status"] = "failed"
    service.workflow.change(task_id, "execution_recovered", reconcile)
    return service.workflow.view(task_id)
