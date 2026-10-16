"""Report configuration presence and installed versions; never print secret values."""
import importlib.metadata
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings


def report():
    settings = Settings.from_env()
    versions = {}
    for package in ("daytona", "openai", "anchorbrowser", "playwright", "arize-phoenix-otel"):
        try: versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: versions[package] = "not installed"
    return {"missing_model_execution": settings.missing_live(),
            "missing_complete_demo": settings.missing_live(browser=True),
            "application_collector_configured": bool(settings.phoenix_endpoint),
            "worker_reachable_collector_configured": bool(settings.worker_collector()),
            "installed_sdk_versions": versions,
            "live_verified": False, "note": "Presence checks do not prove provider connectivity or successful execution."}


if __name__ == "__main__":
    result = report()
    print(json.dumps(result, indent=2))
    if "--require-ready" in sys.argv:
        sys.exit(bool(result["missing_complete_demo"] or not result["worker_reachable_collector_configured"]))
