"""Short-lived task/operation capabilities. No tokens in URLs or access logs."""
import base64
import hashlib
import hmac
import json
import time

from .workflow import WorkflowError


class PortalTokens:
    def __init__(self, key): self.key = key

    def mint(self, task_id, execution_id, scope, seconds=600):
        if len(self.key) < 32: raise WorkflowError("PORTAL_SIGNING_KEY_REQUIRED", 503)
        payload = base64.urlsafe_b64encode(json.dumps({"task_id": task_id, "execution_id": execution_id,
            "scope": scope, "expires": int(time.time()) + seconds}, sort_keys=True).encode()).decode().rstrip("=")
        signature = hmac.new(self.key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + signature

    def verify(self, token, task_id, scope=None):
        try:
            payload, signature = token.split(".")
            expected = hmac.new(self.key.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if len(self.key) < 32 or not hmac.compare_digest(signature, expected): raise ValueError()
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if data["task_id"] != task_id or data["expires"] <= time.time(): raise ValueError()
            if data["scope"] not in {"draft", "submit"} or (scope and data["scope"] != scope): raise ValueError()
            return data
        except (ValueError, KeyError, TypeError):
            raise WorkflowError("PORTAL_CAPABILITY_REQUIRED", 403) from None

    def access(self, request, task_id, scope=None):
        header = request.headers.get("authorization", "")
        if getattr(request.state, "portal_only", False) or header:
            if not header.startswith("Bearer "): raise WorkflowError("PORTAL_CAPABILITY_REQUIRED", 403)
            return self.verify(header[7:], task_id, scope)
        return None  # Private clinician app retains the existing manual reference workflow.
