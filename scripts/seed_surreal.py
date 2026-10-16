"""Repeatable baseline seeding. Never deletes workflow records."""
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import Settings
from app.repository import SurrealRepository


def main():
    settings = Settings.from_env()
    if urlparse(settings.surreal_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("This seed utility is local-only. Use a scoped account for remote databases.")
    dataset = json.loads((ROOT / "data/dataset.json").read_text(encoding="utf-8"))
    repo = SurrealRepository(settings)
    repo.seed((ROOT / "data/seed.surql").read_text(encoding="utf-8"), dataset["snapshot_at"])
    print(json.dumps(repo.list_patients(), indent=2))


if __name__ == "__main__":
    main()
