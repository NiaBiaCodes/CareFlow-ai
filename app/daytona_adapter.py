"""Real SDK operations; the app alone owns sandbox lifecycle and persistence."""
import json

from .config import ROOT

UPLOADS = ("app/__init__.py", "app/comparison.py", "worker/__init__.py",
           "worker/contracts.py", "worker/model.py", "worker/run_task.py", "worker/telemetry.py", "worker/browser.py")


class WorkerFailure(RuntimeError):
    def __init__(self, result):
        code = result.get("error", "UNKNOWN")
        self.code = code if isinstance(code, str) and code.replace("_", "").isalnum() else "UNKNOWN"
        self.telemetry = result.get("telemetry", {})
        super().__init__("WORKER_FAILED_" + self.code)


class DaytonaAdapter:
    def __init__(self, settings):
        from daytona import Daytona, DaytonaConfig
        self.settings = settings
        self.client = Daytona(DaytonaConfig(api_key=settings.daytona_key))

    def create(self, task_id, purpose="draft"):
        from daytona import CreateSandboxFromSnapshotParams
        env = {"ANCHOR_API_KEY": self.settings.anchor_key,
               "PHOENIX_COLLECTOR_ENDPOINT": self.settings.worker_collector(),
               "PHOENIX_API_KEY": self.settings.worker_phoenix_key or self.settings.phoenix_key}
        if purpose == "draft": env.update(OPENAI_API_KEY=self.settings.model_key, LLM_MODEL=self.settings.model)
        params = CreateSandboxFromSnapshotParams(language="python", env_vars=env,
            labels={"careflow_task": task_id}, auto_stop_interval=5, auto_delete_interval=0, ttl_minutes=10,
            **({"snapshot": self.settings.daytona_snapshot} if self.settings.daytona_snapshot else {}))
        return self.client.create(params, timeout=60)

    def prepare(self, sandbox):
        # No .env, expectations, full dataset, database credentials, or other patients.
        sandbox.process.exec("mkdir -p /tmp/careflow/app /tmp/careflow/worker", timeout=15)
        for name in UPLOADS:
            sandbox.fs.upload_file(str(ROOT / name), "/tmp/careflow/" + name, timeout=30)
        sandbox.fs.upload_file(str(ROOT / "requirements.worker.txt"), "/tmp/careflow/requirements.txt", timeout=30)
        install = sandbox.process.exec("python -m pip install --disable-pip-version-check -q -r /tmp/careflow/requirements.txt", timeout=120)
        if install.exit_code != 0:
            raise RuntimeError("WORKER_DEPENDENCY_INSTALL_FAILED")

    def execute(self, sandbox, manifest):
        sandbox.fs.upload_file(json.dumps(manifest).encode(), "/tmp/careflow/manifest.json", timeout=30)
        result = sandbox.process.exec(
            "cd /tmp/careflow && python -m worker.run_task /tmp/careflow/manifest.json /tmp/careflow/result.json",
            timeout=self.settings.execution_timeout)
        if result.exit_code != 0:
            raise RuntimeError("DAYTONA_WORKER_EXECUTION_FAILED")
        data = sandbox.fs.download_file("/tmp/careflow/result.json", 30)
        result = json.loads(data)
        if not result.get("ok"):
            raise WorkerFailure(result)
        return result

    def delete(self, sandbox):
        self.client.delete(sandbox, timeout=60, wait=True)

    def get(self, sandbox_id):
        return self.client.get(sandbox_id, request_timeout=20)

    def find(self, task_id):
        from daytona import ListSandboxesQuery
        return list(self.client.list(ListSandboxesQuery(labels={"careflow_task": task_id}), request_timeout=20))

    def journal(self, sandbox):
        try: return json.loads(sandbox.fs.download_file("/tmp/careflow/browser-journal.json", 15))
        except Exception: return None
