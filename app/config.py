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
    daytona_key: str = field(default="", repr=False)
    daytona_snapshot: str = ""
    model_key: str = field(default="", repr=False)
    model: str = ""
    anchor_key: str = field(default="", repr=False)
    phoenix_endpoint: str = ""
    phoenix_key: str = field(default="", repr=False)
    worker_phoenix_endpoint: str = ""
    worker_phoenix_key: str = field(default="", repr=False)
    payer_url: str = ""
    portal_signing_key: str = field(default="", repr=False)
    execution_timeout: int = 180

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
            daytona_key=os.getenv("DAYTONA_API_KEY", ""),
            daytona_snapshot=os.getenv("DAYTONA_SNAPSHOT", ""),
            model_key=os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
            anchor_key=os.getenv("ANCHOR_API_KEY", ""),
            phoenix_endpoint=os.getenv("PHOENIX_COLLECTOR_ENDPOINT", ""),
            phoenix_key=os.getenv("PHOENIX_API_KEY", ""),
            worker_phoenix_endpoint=os.getenv("PHOENIX_WORKER_COLLECTOR_ENDPOINT", ""),
            worker_phoenix_key=os.getenv("PHOENIX_WORKER_API_KEY", ""),
            payer_url=os.getenv("MOCK_PAYER_BASE_URL", "").rstrip("/"),
            portal_signing_key=os.getenv("PORTAL_SIGNING_KEY", ""),
            execution_timeout=int(os.getenv("CAREFLOW_EXECUTION_TIMEOUT", "180")),
        )

    def missing_live(self, browser=False):
        pairs = [("DAYTONA_API_KEY", self.daytona_key), ("OPENAI_API_KEY or LLM_API_KEY", self.model_key),
                 ("LLM_MODEL", self.model)]
        if browser:
            pairs += [("ANCHOR_API_KEY", self.anchor_key), ("MOCK_PAYER_BASE_URL", self.payer_url),
                      ("PORTAL_SIGNING_KEY", self.portal_signing_key)]
        missing = [name for name, value in pairs if not value]
        if browser and self.portal_signing_key and len(self.portal_signing_key) < 32:
            missing.append("PORTAL_SIGNING_KEY (at least 32 characters)")
        if browser and self.payer_url:
            parsed = urlparse(self.payer_url)
            if parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"} or parsed.query or parsed.username:
                missing.append("MOCK_PAYER_BASE_URL (public HTTPS without credentials)")
        return missing

    def worker_collector(self):
        endpoint = self.worker_phoenix_endpoint or self.phoenix_endpoint
        if urlparse(endpoint).hostname in {"localhost", "127.0.0.1", "::1"}: return ""
        return endpoint
