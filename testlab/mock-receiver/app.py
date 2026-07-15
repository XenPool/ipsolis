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


@app.post("/slack")
async def slack(request: Request) -> Response:
    """Pretend to be a Slack Incoming Webhook (returns the literal ``ok``)."""
    await _record(request, "/slack")
    return Response(content="ok", media_type="text/plain")


# ──────────────────────────────────────────────────────────────────────
# Mock Microsoft Graph — just enough for the entra_group access target:
# an app-only token, a user-id resolve, and group member add/remove.
# Point ipSolis at it via graph.token_url / graph.base_url (testlab seed).
# Member ops are recorded under /graph/members so tests can assert them.
# ──────────────────────────────────────────────────────────────────────

@app.post("/graph/token")
async def graph_token(request: Request) -> dict[str, Any]:
    await _record(request, "/graph/token")
    return {"token_type": "Bearer", "expires_in": 3600, "access_token": "mock-graph-token"}


@app.get("/graph/v1.0/users")
async def graph_users_filter(request: Request) -> dict[str, Any]:
    # $filter fallback lookup — echo a deterministic id for the filtered user.
    await _record(request, "/graph/users")
    return {"value": [{"id": "u-filtered"}]}


@app.get("/graph/v1.0/users/{ident}")
async def graph_user(ident: str, request: Request) -> dict[str, Any]:
    await _record(request, "/graph/users")
    # Deterministic id derived from the principal so add/remove correlate.
    return {"id": f"u-{ident.lower()}"}


async def _record_member_op(request: Request, op: str, group: str, user: str) -> None:
    body_bytes = await request.body()
    _recent.append({
        "ts": time.time(), "path": "/graph/members", "method": request.method,
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "query": {"op": op, "group": group, "user": user},
        "body": body_bytes.decode("utf-8", "replace") if body_bytes else "",
    })
    _counts["/graph/members"] = _counts.get("/graph/members", 0) + 1


@app.post("/graph/v1.0/groups/{gid}/members/$ref")
async def graph_add_member(gid: str, request: Request) -> Response:
    import json as _json
    body = await request.body()
    uid = ""
    try:
        ref = _json.loads(body).get("@odata.id", "")
        uid = ref.rsplit("/", 1)[-1]
    except Exception:  # noqa: BLE001
        pass
    await _record_member_op(request, "add", gid, uid)
    return Response(status_code=204)


@app.delete("/graph/v1.0/groups/{gid}/members/{uid}/$ref")
async def graph_remove_member(gid: str, uid: str, request: Request) -> Response:
    await _record_member_op(request, "remove", gid, uid)
    return Response(status_code=204)


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
