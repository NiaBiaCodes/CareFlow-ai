"""Send a reference validation trace to an already running Arize Phoenix collector."""
import argparse
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from phoenix.otel import register
from opentelemetry import trace
from core import load_json, plan_request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--case", default="missing_referral")
args = parser.parse_args()
if args.case not in load_json(ROOT / "data/expectations.json"):
    parser.error("Unknown case")
provider = register(project_name="careflow-demo", protocol="http/protobuf",
    endpoint=os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces"),
    auto_instrument=False)
tracer = trace.get_tracer("careflow.reference")
with tracer.start_as_current_span("careflow.fixture_validation") as span:
    bundle = load_json(ROOT / "data/cases" / (args.case + ".json"))
    result = plan_request(bundle)
    span.set_attribute("openinference.span.kind", "CHAIN")
    span.set_attribute("careflow.patient_id", result["patient_id"])
    span.set_attribute("careflow.fixture_case", args.case)
    span.set_attribute("careflow.status", result["status"])
    span.set_attribute("careflow.validation.blocked", result["status"] == "blocked")
    span.set_attribute("careflow.validation.errors", result["errors"])
    span.set_attribute("careflow.submitted", False)
if not provider.force_flush(timeout_millis=10000):
    raise SystemExit("Trace flush timed out. Check the collector and inspect Phoenix before retrying.")
provider.shutdown()
print("Trace export completed; verify the careflow-demo project in Phoenix.")
