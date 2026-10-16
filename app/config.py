import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    surreal_url: str = "http://127.0.0.1:8001"
    namespace: str = "careflow"
    database: str = "demo"
    username: str = "root"
    password: str = field(default="", repr=False)
    demo_mode: bool = True

    def __post_init__(self):
        parsed = urlparse(self.surreal_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("SURREAL_HTTP_URL must be an HTTP(S) URL without credentials")
        if not self.namespace or not self.database:
            raise ValueError("SurrealDB namespace and database are required")

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        return cls(
            surreal_url=os.getenv("SURREAL_HTTP_URL", "http://127.0.0.1:8001").rstrip("/"),
            namespace=os.getenv("SURREAL_NAMESPACE", "careflow"),
            database=os.getenv("SURREAL_DATABASE", "demo"),
            username=os.getenv("SURREAL_USER", "root"),
            password=os.getenv("SURREAL_PASSWORD", ""),
            demo_mode=os.getenv("CAREFLOW_DEMO_MODE", "true").lower() == "true",
        )
