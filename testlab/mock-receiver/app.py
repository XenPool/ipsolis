"""Mock SIEM/webhook receiver for the ipSolis testlab.

Records every POST it receives so smoke tests can assert "did we send the
right JSON shape with the right headers". Endpoints are deliberately
unauthenticated — this is lab-only.

Routes:
  POST /sentinel       — pretend to be the Azure Monitor Data Collector API
  POST /splunk         — pretend to be Splunk HEC (when you don't want full Splunk)
  POST /generic        — generic JSON sink (for the ``generic_http`` SIEM adapter)
  POST /teams          — pretend to be a Teams Incoming Webhook
  POST /servicenow     — pretend to be ServiceNow's webhook endpoint

Inspection:
  GET  /recent[?path=/sentinel&limit=20]  — last N requests as JSON
  GET  /count                              — request counts per path
  POST /reset                              — clear the recorded buffer
  GET  /health                             — liveness probe

The buffer is in-memory only and capped at 500 records to keep RSS bounded.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

from fastapi import FastAPI, Request, Response

app = FastAPI(title="ipSolis Testlab Mock Receiver")

_BUFFER_MAX = 500
_recent: deque[dict[str, Any]] = deque(maxlen=_BUFFER_MAX)
_counts: dict[str, int] = {}


async def _record(request: Request, path: str) -> None:
    body_bytes = await request.body()
    try:
        body_text = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        body_text = repr(body_bytes)
    _recent.append({
        "ts": time.time(),
        "path": path,
        "method": request.method,
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "query": dict(request.query_params),
        "body": body_text,
    })
    _counts[path] = _counts.get(path, 0) + 1


@app.post("/sentinel")
async def sentinel(request: Request) -> dict[str, str]:
    await _record(request, "/sentinel")
    return {"status": "ok"}


@app.post("/splunk")
async def splunk(request: Request) -> dict[str, Any]:
    await _record(request, "/splunk")
    return {"text": "Success", "code": 0}


@app.post("/generic")
async def generic(request: Request) -> dict[str, str]:
    await _record(request, "/generic")
    return {"status": "ok"}


@app.post("/teams")
async def teams(request: Request) -> Response:
    await _record(request, "/teams")
    return Response(content="1", media_type="text/plain")


@app.post("/servicenow")
async def servicenow(request: Request) -> dict[str, str]:
    await _record(request, "/servicenow")
    return {"result": "queued"}


@app.get("/recent")
async def recent(path: str | None = None, limit: int = 20) -> dict[str, Any]:
    items = list(_recent)
    if path:
        items = [i for i in items if i["path"] == path]
    items.reverse()
    return {"count": len(items), "items": items[: max(1, min(limit, _BUFFER_MAX))]}


@app.get("/count")
async def count() -> dict[str, Any]:
    return {"counts": dict(_counts), "total": sum(_counts.values())}


@app.post("/reset")
async def reset() -> dict[str, str]:
    _recent.clear()
    _counts.clear()
    return {"status": "reset"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
