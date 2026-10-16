import json
import os
import shutil
import subprocess
import sys

from app.config import ROOT
from app.daytona_adapter import UPLOADS
from core import patient_bundle


def test_uploaded_worker_is_self_contained_and_rejects_patient_mismatch_before_provider(tmp_path, dataset):
    for name in UPLOADS:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    assert not any("data/" in name or ".env" in name for name in UPLOADS)
    manifest = {"task_id": "test-task", "patient_id": "SYN-P002", "operation": "answer",
                "context": patient_bundle(dataset, "SYN-P001")}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI", "LLM_", "DAYTONA", "ANCHOR", "PHOENIX"))}
    result = subprocess.run([sys.executable, "-m", "worker.run_task", "manifest.json", "result.json"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    output = json.loads((tmp_path / "result.json").read_text())
    assert output["ok"] is False and output["error"] == "PATIENT_MISMATCH"
    assert output["telemetry"]["status"] == "not_configured"
