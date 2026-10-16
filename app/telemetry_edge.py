"""Optional authenticated OTLP-only relay. It exposes no Phoenix UI or query API."""
import hmac
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
from dotenv import load_dotenv

from .config import ROOT

load_dotenv(ROOT / ".env", override=False)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.post("/v1/traces")
async def export(request: Request):
    key = os.getenv("PHOENIX_PROXY_TOKEN", "")
    if len(key) < 32 or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + key):
        return Response(status_code=403)
    if request.headers.get("content-type") != "application/x-protobuf": return Response(status_code=415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024: return Response(status_code=413)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            result = await client.post("http://127.0.0.1:6006/v1/traces", content=bytes(body),
                headers={"Content-Type": "application/x-protobuf"})
        return Response(status_code=result.status_code, content=result.content, media_type="application/x-protobuf")
    except httpx.HTTPError:
        return Response(status_code=503)
