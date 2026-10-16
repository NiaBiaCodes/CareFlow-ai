"""Send an actual synthetic validation span to Phoenix. No cloud worker/model claim."""
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ROOT, Settings
from app.validation import evaluate
from worker.telemetry import Telemetry

settings = Settings.from_env()
bundle = json.loads((ROOT / "data/cases/missing_referral.json").read_text())
result = evaluate(bundle, "SYN-P002")
task_id = "local-validation-" + str(uuid4())
tracing = Telemetry(task_id, settings.phoenix_endpoint or "http://127.0.0.1:6006/v1/traces", settings.phoenix_key)
with tracing.span("careflow.local_validation", **{
    "careflow.patient_id": "SYN-P002", "careflow.validation_result": result["status"],
    "careflow.required_documents_present": not bool(result["errors"]),
    "careflow.validation.errors": result["errors"], "careflow.execution_mode": "local_reference_validation"}): pass
print(json.dumps({"task_id": task_id, **tracing.finish()}, indent=2))
