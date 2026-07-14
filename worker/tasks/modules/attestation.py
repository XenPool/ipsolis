"""Attestation-artifact emission (handover + revocation) for the worker.

Called at the order lifecycle completion points in ``dynamic_runner`` — when a
``provision`` order reaches ``provisioned`` (→ handover) and when a ``delete``
order reaches ``revoked`` (→ revocation certificate; expiry-driven revokes flow
through the same delete-order completion). Everything here is **best-effort**:
wrapped so a failure can never break the order flow, and **idempotent** — at
most one artifact of each kind per order (guarded by an existence check).

The artifact freezes a human-readable ``snapshot`` at emit time so the signed
HTML page renders identically even after the order / asset type later changes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

_ACTOR = "celery:attestation"


def emit_attestation_for_order(db, order_id: int, final_status: str) -> None:
    """Create the handover / revocation artifact for a completed order.

    ``final_status`` is the just-written order status (``provisioned`` /
    ``revoked`` / ``delivered``). No-op for anything else, when the asset
    type hasn't opted in, or when an artifact already exists for this order.
    """
    try:
        if final_status not in ("provisioned", "revoked"):
            return
        row = db.execute(text(
            """
            SELECT o.user_email, o.user_name, o.asset_type_id, o.assigned_asset_id,
                   o.requested_from, o.requested_until, o.provisioned_state,
                   at.name AS asset_type_name,
                   at.requires_handover_ack, at.emit_revocation_certificate
            FROM orders o JOIN asset_types at ON at.id = o.asset_type_id
            WHERE o.id = :id
            """
        ), {"id": order_id}).mappings().first()
        if not row:
            return

        if final_status == "provisioned" and row["requires_handover_ack"]:
            _emit_handover(db, order_id, row)
        elif final_status == "revoked" and row["emit_revocation_certificate"]:
            _emit_revocation(db, order_id, row)
    except Exception as exc:  # noqa: BLE001 — must never break the order
        logger.warning("attestation emit failed for order %s: %s", order_id, exc)


def _already_emitted(db, order_id: int, kind: str) -> bool:
    return db.execute(text(
        "SELECT 1 FROM attestation_artifacts WHERE order_id = :o AND kind = :k LIMIT 1"
    ), {"o": order_id, "k": kind}).first() is not None


def _asset_name(row) -> str | None:
    ps = row["provisioned_state"]
    if isinstance(ps, str):
        try:
            ps = json.loads(ps)
        except ValueError:
            ps = None
    if isinstance(ps, dict):
        binding = ps.get("instance_binding") or {}
        if isinstance(binding, dict) and binding.get("asset_name"):
            return binding["asset_name"]
    return None


def _fmt(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt[:10]
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return str(dt)[:10]


def _insert_artifact(db, *, kind, order_id, row, status, snapshot) -> int:
    res = db.execute(text(
        """
        INSERT INTO attestation_artifacts
            (kind, order_id, asset_type_id, recipient_email, recipient_name,
             status, snapshot, created_at)
        VALUES (:kind, :oid, :atid, :email, :name, :status, CAST(:snap AS json), NOW())
        RETURNING id
        """
    ), {
        "kind": kind, "oid": order_id, "atid": row["asset_type_id"],
        "email": row["user_email"], "name": row["user_name"],
        "status": status, "snap": json.dumps(snapshot),
    }).first()
    return res[0]


# ── Handover (Übergabeprotokoll) ───────────────────────────────────────────────

def _emit_handover(db, order_id: int, row) -> None:
    if _already_emitted(db, order_id, "handover"):
        return
    from tasks.modules.config_reader import get_config
    snapshot = {
        "asset_type_name": row["asset_type_name"],
        "asset_name": _asset_name(row),
        "user_email": row["user_email"],
        "user_name": row["user_name"],
        "granted_from": _fmt(row["requested_from"]),
        "granted_until": _fmt(row["requested_until"]),
        "aup_text": (get_config(db, "attestation.aup_text", "") or "").strip(),
    }
    fid = _insert_artifact(
        db, kind="handover", order_id=order_id, row=row, status="pending", snapshot=snapshot
    )
    _audit(db, fid, "emitted", {"kind": "handover", "order_id": order_id})
    db.commit()
    _email_link(
        db, row["user_email"], row["user_name"], fid,
        subject_kind="handover",
        asset=snapshot["asset_type_name"],
    )


# ── Revocation / disposal certificate ─────────────────────────────────────────

def _emit_revocation(db, order_id: int, row) -> None:
    if _already_emitted(db, order_id, "revocation"):
        return
    # Cite the grants ipSolis just rolled back for this user + asset type.
    removed = db.execute(text(
        """
        SELECT cl.target_type, cl.identifier, cl.principal, cl.state
        FROM order_change_log cl JOIN orders o ON o.id = cl.order_id
        WHERE o.user_email = :email AND o.asset_type_id = :atid
          AND cl.action = 'grant' AND cl.state = 'rolled_back'
        ORDER BY cl.id DESC LIMIT 100
        """
    ), {"email": row["user_email"], "atid": row["asset_type_id"]}).mappings().all()
    snapshot = {
        "asset_type_name": row["asset_type_name"],
        "asset_name": _asset_name(row),
        "user_email": row["user_email"],
        "user_name": row["user_name"],
        "revoked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "removed": [
            {"target_type": r["target_type"], "identifier": r["identifier"],
             "principal": r["principal"], "state": r["state"]}
            for r in removed
        ],
    }
    fid = _insert_artifact(
        db, kind="revocation", order_id=order_id, row=row, status="emitted", snapshot=snapshot
    )
    _audit(db, fid, "emitted", {"kind": "revocation", "order_id": order_id,
                                "removed_count": len(snapshot["removed"])})
    db.commit()
    _email_link(
        db, row["user_email"], row["user_name"], fid,
        subject_kind="revocation",
        asset=snapshot["asset_type_name"],
    )


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _audit(db, artifact_id: int, action: str, new: dict) -> None:
    try:
        from tasks.modules.audit_helper import waudit
        waudit(db, "attestation_artifact", artifact_id, action, new=new, by=_ACTOR)
    except Exception as exc:  # noqa: BLE001
        logger.warning("attestation audit failed for %s: %s", artifact_id, exc)


def _email_link(db, to_email, to_name, artifact_id, *, subject_kind, asset) -> None:
    """Best-effort email carrying the signed attestation URL."""
    if not to_email:
        return
    try:
        from tasks.modules.config_reader import get_config
        from tasks.modules.teams_notify import make_attestation_token
        portal_base = (get_config(db, "portal.base_url", "http://localhost:8000") or "").rstrip("/")
        app_title = get_config(db, "app.title", "ip·Solis") or "ip·Solis"
        token = make_attestation_token(artifact_id)
        url = f"{portal_base}/attestation/{token}"
        if subject_kind == "handover":
            subj = f"[{app_title}] Please acknowledge receipt: {asset}"
            body = (
                f"<p>Hi {to_name or ''},</p>"
                f"<p>Your access to <b>{asset}</b> is ready. Please review the handover "
                "details and acknowledge receipt (and the acceptable-use policy, if shown):</p>"
                f"<p><a href='{url}'>Open handover acknowledgment &rarr;</a></p>"
                "<p>The link is signed and works without a portal login.</p>"
            )
        else:
            subj = f"[{app_title}] Revocation certificate: {asset}"
            body = (
                f"<p>Hi {to_name or ''},</p>"
                f"<p>Your access to <b>{asset}</b> has been removed. A revocation / disposal "
                "certificate has been issued as audit evidence:</p>"
                f"<p><a href='{url}'>View revocation certificate &rarr;</a></p>"
                "<p>Save or print the page for your records; the link is signed.</p>"
            )
        from tasks.modules.notifications import _production_send_html_email, MAIL_FROM
        _production_send_html_email(db, [to_email], None, MAIL_FROM, subj, body)
    except Exception as exc:  # noqa: BLE001 — delivery must never break the flow
        logger.warning("attestation email failed for artifact %s: %s", artifact_id, exc)
