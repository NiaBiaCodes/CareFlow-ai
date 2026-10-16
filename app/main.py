from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .repository import StorageUnavailable, SurrealRepository
from .schemas import CreateTask, SaveDraft, SCENARIO_PATIENTS
from .workflow import Workflow, WorkflowError

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def create_app(repository=None, settings=None):
    settings = settings or Settings.from_env()
    repo = repository if repository is not None else SurrealRepository(settings)
    workflow = Workflow(repo, settings)
    app = FastAPI(title="CareFlow · Synthetic demo", version="0.1.0")
    app.state.repository, app.state.workflow = repo, workflow
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
        return render(request, "clinician.html", patients=repo.list_patients(),
                      tasks=repo.list_tasks(task["patient_id"]),
                      scenarios=SCENARIO_PATIENTS if settings.demo_mode else {}, task=task)

    @app.get("/api/patients")
    def patients():
        return repo.list_patients()

    @app.get("/api/tasks")
    def task_history(patient_id: str | None = None):
        return repo.list_tasks(patient_id)

    @app.post("/api/tasks", status_code=201)
    def create_task(body: CreateTask):
        return workflow.create(body)

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
        return render(request, "payer.html", task=workflow.view(task_id))

    @app.post("/mock-payer/requests/{task_id}/draft", status_code=201)
    def save_draft(task_id: str, body: SaveDraft):
        return workflow.save(task_id, body)

    @app.post("/mock-payer/requests/{task_id}/submit")
    @app.post("/api/tasks/{task_id}/submit")
    @app.post("/api/tasks/{task_id}/approval")
    def submission_disabled(task_id: str):
        raise WorkflowError("PR1_SUBMISSION_AND_APPROVAL_UNAVAILABLE", 403)

    return app


app = create_app()
