from pathlib import Path

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from .config import Settings
from .repository import StorageUnavailable, SurrealRepository
from .schemas import CreateTask, SaveDraft, BrowserEvent, ApproveDraft, SubmitDraft, SCENARIO_PATIENTS
from .workflow import Workflow, WorkflowError
from .execution import LiveExecution
from .portal_security import PortalTokens

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def create_app(repository=None, settings=None, execution_factory=LiveExecution):
    settings = settings or Settings.from_env()
    repo = repository if repository is not None else SurrealRepository(settings)
    workflow = Workflow(repo, settings)
    execution = execution_factory(workflow)
    tokens = PortalTokens(settings.portal_signing_key)
    app = FastAPI(title="CareFlow · Synthetic demo", version="0.1.0")
    app.state.repository, app.state.workflow, app.state.execution = repo, workflow, execution
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.exception_handler(StorageUnavailable)
    async def storage_error(request, exc):
        if not request.url.path.startswith("/api/") and request.method == "GET":
            return templates.TemplateResponse(request=request, name="error.html", context={"message": str(exc)}, status_code=503)
        return JSONResponse(status_code=503, content={"error": "STORAGE_UNAVAILABLE", "detail": str(exc)})

    @app.exception_handler(WorkflowError)
    async def workflow_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"error": exc.code})

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"error": "NOT_FOUND"})

    def render(request, name, **context):
        return templates.TemplateResponse(request=request, name=name, context=context)

    @app.get("/")
    def home(request: Request):
        return render(request, "clinician.html", patients=repo.list_patients(), tasks=repo.list_tasks(),
                      scenarios=SCENARIO_PATIENTS if settings.demo_mode else {}, task=None)

    @app.get("/tasks/{task_id}")
    def task_page(request: Request, task_id: str):
        task = workflow.view(task_id)
        review, review_error = None, None
        if task.get("execution_mode") == "live" and task["status"] == "draft_saved":
            try: review = workflow.review(task_id)
            except WorkflowError as exc: review_error = exc.code
            task = workflow.view(task_id)
        return render(request, "clinician.html", patients=repo.list_patients(),
                      tasks=repo.list_tasks(task["patient_id"]),
                      scenarios=SCENARIO_PATIENTS if settings.demo_mode else {}, task=task,
                      review=review, review_error=review_error)

    @app.get("/api/patients")
    def patients():
        return repo.list_patients()

    @app.get("/api/tasks")
    def task_history(patient_id: str | None = None):
        return repo.list_tasks(patient_id)

    @app.post("/api/tasks", status_code=201)
    def create_task(body: CreateTask, background: BackgroundTasks):
        task = workflow.create(body)
        if task.get("execution_mode") == "live" and task["status"] == "created":
            background.add_task(execution.run, task["task_id"])
        return task

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        return workflow.view(task_id)

    @app.get("/api/tasks/{task_id}/sources/{record_id}")
    def get_source(task_id: str, record_id: str):
        return workflow.source(task_id, record_id)

    @app.get("/tasks/{task_id}/sources/{record_id}")
    def source_page(request: Request, task_id: str, record_id: str):
        source = workflow.source(task_id, record_id)
        return render(request, "source.html", task_id=task_id, source=source)

    @app.get("/mock-payer/requests/{task_id}")
    def payer(request: Request, task_id: str):
        tokens.access(request, task_id)
        return render(request, "payer.html", task=workflow.view(task_id), portal_only=getattr(request.state, "portal_only", False))

    @app.post("/mock-payer/requests/{task_id}/draft", status_code=201)
    def save_draft(request: Request, task_id: str, body: SaveDraft):
        return workflow.save(task_id, body, tokens.access(request, task_id, "draft"))

    @app.post("/mock-payer/requests/{task_id}/events")
    def browser_event(request: Request, task_id: str, body: BrowserEvent):
        access = tokens.access(request, task_id)
        if access is None: raise WorkflowError("PORTAL_CAPABILITY_REQUIRED", 403)
        return workflow.browser_event(task_id, access, body)

    @app.get("/mock-payer/requests/{task_id}/state")
    def portal_state(request: Request, task_id: str):
        tokens.access(request, task_id)
        task = workflow.view(task_id)
        return {"task_id": task_id, "patient_id": task["patient_id"], "status": task["status"],
                "drafts": task["drafts"], "receipt": task.get("receipt")}

    @app.post("/api/tasks/{task_id}/submit")
    def submission_disabled(task_id: str):
        raise WorkflowError("USE_APPROVED_BROWSER_SUBMISSION", 403)

    @app.post("/api/tasks/{task_id}/approval")
    async def approve(request: Request, task_id: str):
        if repo.get_task(task_id).get("execution_mode") != "live":
            raise WorkflowError("LIVE_VERIFIED_DRAFT_REQUIRED", 403)
        try: body = ApproveDraft.model_validate(await request.json())
        except (ValidationError, ValueError): raise WorkflowError("INVALID_APPROVAL_REQUEST", 422) from None
        return workflow.approve(task_id, body)

    @app.post("/api/tasks/{task_id}/execute-submit", status_code=202)
    def execute_submit(task_id: str, background: BackgroundTasks):
        task = repo.get_task(task_id)
        if task.get("receipt"): return {"receipt": task["receipt"], "status": task["status"]}
        workflow.approved_binding(task)
        workflow.require_cleaned(task)
        background.add_task(execution.submit, task_id)
        return {"status": "submission_queued"}

    @app.post("/api/tasks/{task_id}/recover")
    def recover_execution(task_id: str):
        return execution.recover(task_id)

    @app.post("/api/tasks/{task_id}/retry", status_code=202)
    def retry_execution(task_id: str, background: BackgroundTasks):
        task = repo.get_task(task_id)
        if task.get("execution_mode") != "live" or task["status"] not in {"failed", "configuration_missing"}:
            raise WorkflowError("TASK_NOT_RETRYABLE")
        workflow.require_cleaned(task)
        workflow.ensure_current_context(task)
        workflow.change(task_id, "retry_requested", lambda t: t.update(
            status="created", configuration_missing=[], execution_error=None))
        background.add_task(execution.run, task_id)
        return {"status": "retry_queued"}

    @app.post("/mock-payer/requests/{task_id}/submit")
    async def portal_submit(request: Request, task_id: str):
        access = tokens.access(request, task_id, "submit")
        if access is None: raise WorkflowError("SUBMISSION_CAPABILITY_REQUIRED", 403)
        try: body = SubmitDraft.model_validate(await request.json())
        except (ValidationError, ValueError): raise WorkflowError("INVALID_SUBMISSION_REQUEST", 422) from None
        return workflow.submit(task_id, body, access)

    return app


app = create_app()
