"""Prometheus /metrics endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.config import AppConfig
from app.utils import metrics as metrics_util

router = APIRouter(tags=["metrics"])


async def _is_enabled(db: AsyncSession) -> bool:
    row = await db.execute(
        select(AppConfig.value).where(AppConfig.key == "metrics.enabled")
    )
    raw = (row.scalar_one_or_none() or "true").strip().lower()
    return raw not in ("false", "0", "no", "off", "disabled")


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(db: AsyncSession = Depends(get_db)) -> Response:
    """Returns metrics in the Prometheus text exposition format.

    Disable by setting ``metrics.enabled = false`` in ``app_config``. Lock
    down via reverse proxy (nginx ``allow``/``deny`` or basic auth) when
    exposed beyond the cluster perimeter — this endpoint has no built-in
    auth so cAdvisor / kube-prometheus / standalone Prometheus can scrape
    without configuration.
    """
    if not await _is_enabled(db):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="metrics endpoint is disabled",
        )
    payload, content_type = await metrics_util.render(db)
    return Response(content=payload, media_type=content_type)
