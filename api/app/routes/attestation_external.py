"""Tokenized attestation-artifact pages — no portal session required.

The recipient clicks the signed link in their handover / revocation email.
The token identifies the ``AttestationArtifact`` row; we trust it because it's
HMAC-signed with ``API_SECRET_KEY`` (see ``app.utils.attestation_token``).

* ``GET  /attestation/{token}`` — render the handover acknowledgment page
  (with an Acknowledge button while ``pending``) or the read-only revocation
  certificate (archival via browser print).
* ``POST /attestation/{token}`` — record a handover acknowledgment
  (``pending`` → ``acknowledged``), audited. Idempotent-ish: re-acking an
  already-acknowledged row just shows the confirmation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.attestation_artifact import (
    KIND_HANDOVER,
    KIND_REVOCATION,
    AttestationArtifact,
)
from app.templates_instance import templates
from app.utils.attestation_token import verify_attestation_token
from app.utils.audit import aaudit

logger = logging.getLogger(__name__)
router = APIRouter(tags=["attestation-external"])


def _status_page(request, *, title, headline, message, tone="info", status_code=200) -> HTMLResponse:
    return templates.TemplateResponse(
        "review_status.html",
        {"request": request, "title": title, "headline": headline,
         "message": message, "tone": tone},
        status_code=status_code,
    )


async def _load(db: AsyncSession, token: str) -> tuple[AttestationArtifact | None, str]:
    payload = verify_attestation_token(token)
    if payload is None:
        return None, "bad_token"
    artifact = await db.get(AttestationArtifact, payload["aid"])
    if artifact is None:
        return None, "missing_row"
    return artifact, "ok"


@router.get("/attestation/{token}", response_class=HTMLResponse)
async def attestation_get(request: Request, token: str, db: AsyncSession = Depends(get_db)):
    artifact, reason = await _load(db, token)
    if reason == "bad_token":
        return _status_page(
            request, title="Link invalid",
            headline="This attestation link is invalid or has expired.",
            message="Attestation links are signed and valid for 90 days.",
            tone="warning", status_code=410,
        )
    if reason == "missing_row":
        return _status_page(
            request, title="Not found",
            headline="This attestation is no longer in the system.",
            message="The associated order may have been deleted.",
            tone="info", status_code=404,
        )

    snap = artifact.snapshot or {}
    if artifact.kind == KIND_REVOCATION:
        return templates.TemplateResponse(
            "attestation_revocation.html",
            {"request": request, "artifact": artifact, "snap": snap},
        )
    # Handover
    return templates.TemplateResponse(
        "attestation_handover.html",
        {"request": request, "artifact": artifact, "snap": snap, "token": token},
    )


@router.post("/attestation/{token}", response_class=HTMLResponse)
async def attestation_post(
    request: Request,
    token: str,
    acknowledger_name: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    artifact, reason = await _load(db, token)
    if reason == "bad_token":
        raise HTTPException(status_code=410, detail="Attestation link invalid or expired")
    if reason == "missing_row":
        raise HTTPException(status_code=404, detail="Attestation no longer exists")

    if artifact.kind != KIND_HANDOVER:
        # Revocation certificates are evidence-only — nothing to acknowledge.
        raise HTTPException(status_code=400, detail="This attestation cannot be acknowledged")

    if artifact.status == "acknowledged":
        return _status_page(
            request, title="Already acknowledged",
            headline="This handover was already acknowledged.",
            message="No further action is needed.", tone="info",
        )

    now = datetime.now(timezone.utc)
    who = (acknowledger_name or "").strip() or (artifact.recipient_name or artifact.recipient_email or "recipient")
    actor = f"api:attestation_token (recipient:{artifact.recipient_email or '?'})"
    artifact.status = "acknowledged"
    artifact.acknowledged_at = now
    artifact.acknowledged_by = who[:255]

    await aaudit(
        db, "attestation_artifact", artifact.id, "acknowledged",
        new={"order_id": artifact.order_id, "acknowledged_by": who,
             "recipient_email": artifact.recipient_email, "via": "signed_token"},
        by=actor,
    )
    await db.commit()

    return _status_page(
        request, title="Acknowledged",
        headline="Handover acknowledged — thank you.",
        message="Your acknowledgment has been recorded in the audit trail. "
                "You may keep or print this page for your records.",
        tone="success",
    )
