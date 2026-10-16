"""Fixed entrypoint. Operation, patient context and scoped capabilities come from the server."""
import json
import os
import sys
from pathlib import Path

from worker.contracts import assert_patient
from worker.telemetry import Telemetry


def run(manifest):
    assert_patient(manifest["context"], manifest["patient_id"])
    if manifest["operation"] == "answer":
        from worker.model import answer_question
        return answer_question(manifest)
    if manifest["operation"] in {"draft", "submit"}:
        from worker.browser import browser_task
        return browser_task(manifest)
    raise ValueError("UNSUPPORTED_OPERATION")


if __name__ == "__main__":
    manifest_path, result_path = map(Path, sys.argv[1:3])
    manifest = json.loads(manifest_path.read_text())
    telemetry = Telemetry(manifest["task_id"], os.getenv("PHOENIX_COLLECTOR_ENDPOINT", ""),
                          os.getenv("PHOENIX_API_KEY", ""), parent=manifest.get("trace_context"))
    try:
        with telemetry.span("worker." + manifest["operation"], **{
                "careflow.patient_id": manifest["patient_id"],
                "openinference.span.kind": "LLM" if manifest["operation"] == "answer" else "TOOL"}) as span:
            if manifest["operation"] == "answer": span.set_attribute("llm.model_name", os.getenv("LLM_MODEL", ""))
            result = {"ok": True, **run(manifest)}
    except Exception as exc:
        # Provider exceptions may contain request URLs/headers. Persist only class/code.
        result = {"ok": False, "error": type(exc).__name__}
        if isinstance(exc, ValueError) and str(exc).isupper():
            result["error"] = str(exc)
    result["telemetry"] = telemetry.finish()  # Flushed before the server deletes this sandbox.
    result_path.write_text(json.dumps(result))
