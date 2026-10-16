"""Cloud smoke test: run selected fixture logic inside Daytona, download result, delete sandbox.

Requires DAYTONA_API_KEY and the daytona package. May incur provider usage.
This exercises deterministic reference logic; it does not make a model call.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--case", default="happy")
args = parser.parse_args()
expected = json.loads((ROOT / "data/expectations.json").read_text())
if args.case not in expected:
    parser.error("Unknown case")
if not os.getenv("DAYTONA_API_KEY"):
    raise SystemExit("Set DAYTONA_API_KEY before running the cloud smoke test.")
from daytona import Daytona, CreateSandboxFromSnapshotParams

client = Daytona()
sandbox = None
try:
    sandbox = client.create(CreateSandboxFromSnapshotParams(language="python", auto_stop_interval=10, auto_delete_interval=0))
    # Persist this ID in the real workflow store immediately after creation.
    print("Created sandbox", sandbox.id)
    sandbox.fs.upload_file(str(ROOT / "core.py"), "/tmp/careflow_core.py")
    sandbox.fs.upload_file(str(ROOT / "data/cases" / (args.case + ".json")), "/tmp/careflow_bundle.json")
    runner = b'''import json\nfrom pathlib import Path\nfrom careflow_core import plan_request\nb = json.loads(Path("/tmp/careflow_bundle.json").read_text())\nr = plan_request(b)\nPath("/tmp/careflow_result.json").write_text(json.dumps(r, indent=2))\nprint(r["status"])\n'''
    sandbox.fs.upload_file(runner, "/tmp/careflow_run.py")
    run = sandbox.process.exec("python /tmp/careflow_run.py", timeout=60)
    if run.exit_code != 0:
        raise RuntimeError("Sandbox worker failed: " + run.result)
    target_dir = ROOT / "runs"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / (args.case + "_daytona.json")
    sandbox.fs.download_file("/tmp/careflow_result.json", str(target))
    result = json.loads(target.read_text())
    if result["status"] != expected[args.case]["status"] or result["errors"] != expected[args.case]["errors"]:
        raise RuntimeError("Sandbox output disagrees with the independent fixture expectation")
    print("Downloaded", target, "status", result["status"])
finally:
    if sandbox is not None:
        # A failure here must be visible so the caller can retry cleanup by ID.
        sandbox.delete(timeout=60, wait=True)
        print("Sandbox deletion confirmed")
