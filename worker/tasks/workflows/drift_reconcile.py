"""Drift / out-of-band reconciliation Beat task.

ipSolis grants AD group membership fire-and-forget; nothing re-reads the group
afterwards, so it can't tell you if someone was added directly in AD (out of
band) or removed from a group it granted. This task closes that gap.

Two Beat entries, mirroring the backup scheduler:

* ``check_drift_schedule`` — runs every minute, honours ``drift.enabled`` +
  ``drift.schedule_cron`` (croniter), dedups via ``drift.last_run``, and
  enqueues the actual scan when the cron fires.
* ``reconcile_drift`` — for every AD group that a ``drift_monitor`` asset type
  provisions into, compares the *actual* direct membership against what ipSolis
  granted (from ``order_change_log`` over active orders):

    - ``missing_access`` — ipSolis granted it, the principal is NOT in the group.
    - ``out_of_band``    — the principal IS in the group, ipSolis never granted it.

  Each divergence is written to ``drift_findings`` and audit-logged (which the
  SIEM streamer forwards). When ``drift.remediation_mode = auto_remediate`` the
  task also re-grants missing / revokes out-of-band members via the existing
  ``_grant_ad_group`` / ``_revoke_ad_group`` handlers. A summary email (to
  ``health.alert_email``) and a Teams card (if configured) are sent best-effort.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules.audit_helper import waudit
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

_ACTIVE = ("provisioned", "delivered")
_ACTOR = "beat:drift_reconcile"


def _db() -> Session:
    from tasks.modules.db import get_worker_session
    return get_worker_session()


def _bool_cfg(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


# ── Scheduler (Beat every minute) ──────────────────────────────────────────────

@app.task(name="tasks.workflows.drift_reconcile.check_drift_schedule")
def check_drift_schedule() -> dict:
    """Enqueue a drift scan when ``drift.schedule_cron`` fires (opt-in)."""
    try:
        from croniter import croniter
    except Exception as exc:  # noqa: BLE001
        logger.warning("croniter unavailable: %s", exc)
        return {"success": False, "error": "croniter missing"}

    db = _db()
    try:
        if not _bool_cfg(get_config(db, "drift.enabled", "false")):
            return {"success": True, "skipped": "disabled"}

        cron_expr = (get_config(db, "drift.schedule_cron", "0 3 * * *") or "0 3 * * *").strip()
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        try:
            prev_fire = croniter(cron_expr, now).get_prev(datetime)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Invalid drift.schedule_cron=%r: %s", cron_expr, exc)
            return {"success": False, "error": f"invalid cron: {exc}"}
        if prev_fire.tzinfo is None:
            prev_fire = prev_fire.replace(tzinfo=timezone.utc)
        if (now - prev_fire).total_seconds() > 60:
            return {"success": True, "skipped": "not-due"}

        # Dedup: don't enqueue twice for the same fire.
        lr = get_config(db, "drift.last_run", "")
        if lr:
            try:
                last = datetime.fromisoformat(lr)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if last >= prev_fire:
                    return {"success": True, "skipped": "already-run"}
            except ValueError:
                pass

        db.execute(
            text(
                "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) "
                "VALUES ('drift.last_run', :v, NULL, false, NOW(), NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
            ),
            {"v": now.isoformat()},
        )
        db.commit()
        app.send_task("tasks.workflows.drift_reconcile.reconcile_drift", queue="reclaim")
        return {"success": True, "enqueued": True, "fire": prev_fire.isoformat()}
    finally:
        db.close()


# ── Reconciliation ─────────────────────────────────────────────────────────────

def _record_finding(db, atid, oid, group_dn, principal, direction) -> tuple[int, bool]:
    """Get-or-create the open finding for this key. Returns ``(id, is_new)``.

    A key that already has an open finding is reused (no duplicate row, no
    re-audit) — but its id is still returned so an ``auto_remediate`` run can
    act on a finding first detected while in ``detect_only`` mode.
    """
    existing = db.execute(
        text(
            "SELECT id FROM drift_findings WHERE identifier = :g "
            "AND lower(principal) = lower(:p) AND direction = :d AND status = 'open' LIMIT 1"
        ),
        {"g": group_dn, "p": principal, "d": direction},
    ).first()
    if existing:
        return existing[0], False
    row = db.execute(
        text(
            "INSERT INTO drift_findings "
            "(asset_type_id, order_id, target_type, identifier, principal, direction, status, remediation, detected_at) "
            "VALUES (:at, :oid, 'ad_group', :g, :p, :d, 'open', 'detected', NOW()) RETURNING id"
        ),
        {"at": atid, "oid": oid, "g": group_dn, "p": principal, "d": direction},
    ).first()
    db.flush()
    fid = row[0]
    waudit(
        db, "drift_finding", fid, "detected",
        new={"direction": direction, "principal": principal, "group": group_dn},
        by=_ACTOR,
    )
    return fid, True


def _remediate(db, fid, success_state, action_fn, summary) -> None:
    """Run an AD write (grant/revoke); update the finding + audit."""
    try:
        action_fn()
        db.execute(
            text("UPDATE drift_findings SET status='remediated', remediation=:r, resolved_at=NOW() WHERE id=:i"),
            {"r": success_state, "i": fid},
        )
        summary["remediated"] += 1
        waudit(db, "drift_finding", fid, "remediated", new={"remediation": success_state}, by=_ACTOR)
    except Exception as exc:  # noqa: BLE001
        db.execute(
            text("UPDATE drift_findings SET remediation='failed', detail=CAST(:d AS jsonb) WHERE id=:i"),
            {"d": json.dumps({"error": str(exc)[:300]}), "i": fid},
        )
        summary["failed"] += 1
        logger.warning("drift: remediation failed for finding %s: %s", fid, exc)


@app.task(name="tasks.workflows.drift_reconcile.reconcile_drift")
def reconcile_drift() -> dict:
    """Compare AD membership against provisioned grants; record + alert + remediate."""
    from tasks.modules.target_executor import (
        list_ad_group_members, _grant_ad_group, _revoke_ad_group,
    )

    db = _db()
    try:
        mode = (get_config(db, "drift.remediation_mode", "detect_only") or "detect_only").strip()
        auto = mode == "auto_remediate"
        bind_user = (get_config(db, "ad.username", "") or "").strip().lower()

        # Groups provisioned by a drift-monitored asset type (via active orders).
        grp_rows = db.execute(text(
            "SELECT DISTINCT cl.identifier, at.id FROM order_change_log cl "
            "JOIN orders o ON o.id = cl.order_id JOIN asset_types at ON at.id = o.asset_type_id "
            "WHERE cl.target_type='ad_group' AND cl.action='grant' AND cl.state='success' "
            "AND at.drift_monitor = true AND o.status::text = ANY(:st)"
        ), {"st": list(_ACTIVE)}).fetchall()
        group_to_type: dict[str, int] = {}
        for ident, atid in grp_rows:
            group_to_type.setdefault(ident, atid)

        if not group_to_type:
            return {"success": True, "monitored_groups": 0, "findings": 0}

        # "Should have" per group across ALL active orders (any type) so a group
        # legitimately granted via a non-monitored type isn't flagged out-of-band.
        sh_rows = db.execute(text(
            "SELECT cl.identifier, cl.principal, MIN(cl.order_id) AS order_id FROM order_change_log cl "
            "JOIN orders o ON o.id = cl.order_id "
            "WHERE cl.target_type='ad_group' AND cl.action='grant' AND cl.state='success' "
            "AND o.status::text = ANY(:st) GROUP BY cl.identifier, cl.principal"
        ), {"st": list(_ACTIVE)}).fetchall()
        should_have: dict[str, dict[str, tuple]] = {}
        for ident, principal, oid in sh_rows:
            if principal:
                should_have.setdefault(ident, {})[principal.strip().lower()] = (oid, principal)

        summary = {
            "monitored_groups": 0, "missing_access": 0, "out_of_band": 0,
            "remediated": 0, "failed": 0, "errors": [],
        }
        new_findings: list[tuple] = []

        for group_dn, atid in group_to_type.items():
            summary["monitored_groups"] += 1
            try:
                members = list_ad_group_members(group_dn, db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("drift: member read failed for %s: %s", group_dn, exc)
                summary["errors"].append({"group": group_dn, "error": str(exc)[:200]})
                continue

            ad_ids: set[str] = set()
            ad_by_email: dict[str, dict] = {}
            for m in members:
                em = (m.get("mail") or "").strip().lower()
                sam = (m.get("sam") or "").strip().lower()
                if em:
                    ad_ids.add(em)
                    ad_by_email[em] = m
                if sam:
                    ad_ids.add(sam)

            want = should_have.get(group_dn, {})

            # missing_access: ipSolis granted, principal not in AD.
            for p_lower, (oid, p_orig) in want.items():
                if p_lower not in ad_ids:
                    fid, is_new = _record_finding(db, atid, oid, group_dn, p_orig, "missing_access")
                    if is_new:
                        summary["missing_access"] += 1
                        new_findings.append(("missing_access", p_orig, group_dn))
                    if auto:
                        _remediate(db, fid, "re_granted",
                                   lambda p=p_orig: _grant_ad_group(group_dn, p, db), summary)

            # out_of_band: in AD, ipSolis never granted (excluding the bind account).
            for em, m in ad_by_email.items():
                sam = (m.get("sam") or "").strip().lower()
                if em in want or em == bind_user or sam == bind_user:
                    continue
                principal = m.get("mail") or m.get("sam")
                if not principal:
                    continue
                fid, is_new = _record_finding(db, atid, None, group_dn, principal, "out_of_band")
                if is_new:
                    summary["out_of_band"] += 1
                    new_findings.append(("out_of_band", principal, group_dn))
                if auto:
                    _remediate(db, fid, "revoked",
                               lambda p=principal: _revoke_ad_group(group_dn, p, db), summary)

            db.commit()

        if new_findings:
            _alert(db, summary, new_findings)

        logger.info(
            "drift: %d group(s), %d missing, %d out-of-band, %d remediated, %d failed",
            summary["monitored_groups"], summary["missing_access"],
            summary["out_of_band"], summary["remediated"], summary["failed"],
        )
        return {"success": True, **summary}
    finally:
        db.close()


def _alert(db, summary, new_findings) -> None:
    """Best-effort email + Teams summary. SIEM is covered by the audit rows."""
    lines = "\n".join(
        f"- {d.replace('_', ' ')}: {p} @ {g}" for d, p, g in new_findings[:50]
    )
    subj = (
        f"[ipSolis] Drift detected: {summary['missing_access']} missing, "
        f"{summary['out_of_band']} out-of-band"
    )
    try:
        to_addr = (get_config(db, "health.alert_email", "") or "").strip()
        if to_addr:
            from tasks.modules.notifications import _production_send_html_email, MAIL_FROM
            body = (
                "<p>Drift reconciliation found the following divergences between "
                "provisioned access and Active Directory:</p>"
                f"<pre style='font-size:13px'>{lines}</pre>"
                "<p>See <b>Operations → Drift</b> in the admin UI.</p>"
            )
            _production_send_html_email(db, [to_addr], None, MAIL_FROM, subj, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("drift: email alert failed: %s", exc)

    try:
        if (get_config(db, "teams.mode", "disabled") or "").strip() == "enabled":
            url = (get_config(db, "teams.webhook_url", "") or "").strip()
            if url and not url.startswith(("vault://", "ccp://", "azurekv://", "awssm://", "conjur://")):
                from tasks.modules.teams_notify import post_adaptive_card
                card = {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": subj},
                        {"type": "TextBlock", "wrap": True, "text": lines.replace("\n", "\n\n")},
                    ],
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                }
                post_adaptive_card(url, card)
    except Exception as exc:  # noqa: BLE001
        logger.warning("drift: teams alert failed: %s", exc)
