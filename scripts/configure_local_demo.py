"""Generate only missing local capability secrets. Never overwrites provider keys."""
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

root = Path(__file__).resolve().parents[1]
path = root / ".env"
values = dotenv_values(path)
for name in ("PORTAL_SIGNING_KEY", "PHOENIX_PROXY_TOKEN"):
    if not values.get(name):
        set_key(path, name, secrets.token_urlsafe(48))
        print(name + " generated in ignored .env")
    else:
        print(name + " already configured")
