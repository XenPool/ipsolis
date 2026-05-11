"""Celery App – entry point for worker, beat, and flower."""

import importlib.util
import os

from celery import Celery
from celery.schedules import crontab

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# Community-edition workflows — always present in both Community and PRO images.
_COMMUNITY_INCLUDE = [
    "tasks.workflows.dynamic_runner",
    "tasks.workflows.ps_module_installer",
    "tasks.workflows.license_check",
    "tasks.workflows.approval_reminders",
    "tasks.workflows.approval_auto_decline",
    "tasks.workflows.cost_threshold_alerter",
    "tasks.workflows.cost_report_snapshot",
    "tasks.workflows.audit_retention",
    "tasks.workflows.api_token_purge",
    "tasks.workflows.update_checker",
    "tasks.modules.maintenance",
]

# PRO-only workflows — absent in Community images. Celery only loads them when present.
_BUSINESS_INCLUDE = [
    "tasks.workflows.standalone_runner",
    "tasks.workflows.sccm_probe",
    "tasks.workflows.siem_streamer",
    "tasks.workflows.certification_notifications",
    "tasks.workflows.certification_reminders",
]

_include = _COMMUNITY_INCLUDE + [
    m for m in _BUSINESS_INCLUDE
    if importlib.util.find_spec(m) is not None
]

app = Celery(
    "ipsolis_worker",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=_include,
)

# OpenTelemetry tracing — opt-in via otel.* config keys. Must run before the
# Celery workers fork so the instrumentor wires into the task signals.
try:
    from tasks.tracing import setup_worker_tracing
    setup_worker_tracing()
except Exception:
    # Tracing setup failures must never block worker startup.
    import logging
    logging.getLogger(__name__).exception("Worker tracing setup failed")


# ── Per-fork install_uuid registration ───────────────────────────────────────
# The license verifier enforces install-bound licenses by comparing the
# license's ``install_uuid`` field against a process-local value. Each
# pre-forked worker process needs its own register call (the cache lives
# in module-level globals which don't survive fork as written values, only
# as code). We hook ``worker_process_init`` so the register runs once per
# fork before any task starts.
from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def _register_install_uuid(**_kwargs):
    import logging
    log = logging.getLogger(__name__)
    try:
        # Sync DB read — same engine pattern used in tasks.modules.maintenance.
        from sqlalchemy import create_engine, text
        url = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        if not url:
            log.warning("DATABASE_URL not set; install_uuid will fall back to None")
            from tasks.utils.license import set_install_uuid
            set_install_uuid(None)
            return
        engine = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM app_config WHERE key = 'install.uuid'")
            ).fetchone()
        engine.dispose()
        from tasks.utils.license import set_install_uuid
        set_install_uuid(row[0] if row else None)
    except Exception:
        log.exception("install_uuid registration failed; install-bound licenses will fail closed")
        try:
            from tasks.utils.license import set_install_uuid
            set_install_uuid(None)
        except Exception:
            pass

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Berlin",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,                     # ACK only after successful completion
    worker_prefetch_multiplier=1,            # No prefetch accumulation for long-running tasks

    # ── celery-redbeat: HA Beat scheduler ────────────────────────────────────
    # Redis-backed schedule store + Lua-script distributed lock means N Beat
    # replicas can run side-by-side and only the lock-holder dispatches. The
    # static ``beat_schedule`` dict below is loaded into Redis on first start
    # and re-synced on every restart, so changes here ship via container
    # rebuild as before.
    #
    # Failover timing: ``redbeat_lock_timeout`` is how long the dead lock
    # stays in Redis before another replica can claim it.
    # ``beat_max_loop_interval`` caps how long a non-leader sleeps before
    # re-checking the lock. We set both to 30s so a hard kill of the active
    # replica produces a sub-minute leader handover; default RedBeat polls
    # only every 5 min, which yields ~5 min failover and isn't really HA.
    redbeat_redis_url=BROKER_URL,
    redbeat_lock_timeout=30,                 # seconds — dead-lock TTL in Redis
    beat_max_loop_interval=30,               # seconds — non-leader poll cadence
    redbeat_key_prefix="ipsolis:redbeat:",   # namespace so multiple ipSolis
                                             # tenants on a shared Redis don't
                                             # collide on schedule keys
    task_routes={
        "tasks.workflows.dynamic_runner.*": {"queue": "provision"},
        "tasks.workflows.ps_module_installer.*": {"queue": "provision"},
        "tasks.workflows.standalone_runner.*": {"queue": "provision"},
        "tasks.workflows.license_check.*": {"queue": "default"},
        "tasks.workflows.siem_streamer.*": {"queue": "default"},
        "tasks.workflows.audit_retention.*": {"queue": "default"},
        "tasks.workflows.api_token_purge.*": {"queue": "default"},
        "tasks.workflows.update_checker.*": {"queue": "default"},
        "tasks.workflows.approval_reminders.*": {"queue": "notifications"},
        "tasks.workflows.approval_auto_decline.*": {"queue": "notifications"},
        "tasks.workflows.cost_threshold_alerter.*": {"queue": "notifications"},
        "tasks.workflows.cost_report_snapshot.*": {"queue": "default"},
        "tasks.workflows.certification_notifications.*": {"queue": "notifications"},
        "tasks.workflows.certification_reminders.*": {"queue": "notifications"},
        "tasks.modules.notifications.*": {"queue": "notifications"},
        "tasks.modules.maintenance.*": {"queue": "default"},
    },
    beat_schedule={
        # Check hourly expiring assets + send reminder emails
        "check-expiring-assets": {
            "task": "tasks.workflows.dynamic_runner.check_expiring_assets",
            "schedule": crontab(minute=0),  # Every full hour
            "options": {"queue": "reclaim"},
        },
        # Dispatch scheduled orders whose start date has arrived
        "check-scheduled-orders": {
            "task": "tasks.workflows.dynamic_runner.check_scheduled_orders",
            "schedule": crontab(minute=0),  # Every full hour
            "options": {"queue": "provision"},
        },
        # Re-dispatch deprovision tasks for orders stuck in 'revoking' with no
        # active step — catches silent task failures (e.g. DB connection exhaustion).
        "recover-stuck-revoking": {
            "task": "tasks.workflows.dynamic_runner.recover_stuck_revoking",
            "schedule": crontab(minute="*/5"),  # Every 5 minutes
            "options": {"queue": "reclaim"},
        },
        # Dispatch cron-scheduled standalone runbooks (PRO only)
        **({
            "dispatch-standalone-cron": {
                "task": "tasks.workflows.standalone_runner.check_cron_schedules",
                "schedule": crontab(minute="*"),  # Every minute
                "options": {"queue": "provision"},
            },
        } if "tasks.workflows.standalone_runner" in _include else {}),
        # Scheduled database backups (cron-expression driven)
        "maintenance-backup-scheduler": {
            "task": "tasks.modules.maintenance.check_backup_schedule",
            "schedule": crontab(minute="*"),  # Every minute
            "options": {"queue": "default"},
        },
        # Health probe transitions → email alerts
        "maintenance-health-alert": {
            "task": "tasks.modules.maintenance.check_health_and_alert",
            "schedule": crontab(minute="*/5"),  # Every 5 minutes
            "options": {"queue": "default"},
        },
        # Daily license expiry check (30/14/7 day warnings + expired error)
        "license-expiry-check": {
            "task": "tasks.workflows.license_check.check_license_expiry",
            "schedule": crontab(hour=8, minute=0),  # Daily at 08:00 Europe/Berlin
            "options": {"queue": "default"},
        },
        # Stream new audit_log rows to the configured SIEM endpoint
        "siem-stream-audit-log": {
            "task": "tasks.workflows.siem_streamer.stream_audit_log",
            "schedule": crontab(minute="*"),  # Every minute
            "options": {"queue": "default"},
        },
        # Prune audit_log rows past the configured retention window
        "audit-retention-prune": {
            "task": "tasks.workflows.audit_retention.prune_old_rows",
            "schedule": crontab(hour=3, minute=0),  # Daily at 03:00 Europe/Berlin
            "options": {"queue": "default"},
        },
        # Hard-delete revoked / expired API tokens past the configured
        # window (api_tokens.purge_after_days). Opt-in — no-op when 0.
        # Slot at :15 sits between audit retention (03:00) and approval
        # auto-decline (03:30) so the daily housekeeping window stays
        # contained.
        "api-token-purge-daily": {
            "task": "tasks.workflows.api_token_purge.purge_old_tokens",
            "schedule": crontab(hour=3, minute=15),  # Daily at 03:15 Europe/Berlin
            "options": {"queue": "default"},
        },
        # Re-notify approvers who have not yet decided on stale requests
        "approval-reminder-scan": {
            "task": "tasks.workflows.approval_reminders.scan_and_remind",
            "schedule": crontab(minute=15),  # Hourly at :15 to spread Beat load
            "options": {"queue": "notifications"},
        },
        # Decline pending approvals past the configured inactivity window
        # (opt-in via approval.auto_decline_enabled — no-op when disabled).
        # Daily cadence is plenty since the threshold is in days.
        "approval-auto-decline-scan": {
            "task": "tasks.workflows.approval_auto_decline.scan_and_auto_decline",
            "schedule": crontab(hour=3, minute=30),  # Daily at 03:30 Europe/Berlin
            "options": {"queue": "notifications"},
        },
        # Alert when projected monthly spend per (cost_center, currency)
        # crosses a configured threshold. No-op when no thresholds are
        # configured. Hysteresis via cost.threshold_alert_quiet_hours
        # keeps a hovering spend from spamming alerts.
        "cost-threshold-alerter": {
            "task": "tasks.workflows.cost_threshold_alerter.scan_and_alert",
            "schedule": crontab(hour=4, minute=0),  # Daily at 04:00 Europe/Berlin
            "options": {"queue": "notifications"},
        },
        # Snapshot the cost report views into cost_report_snapshots so the
        # ``?as_of=`` query path on the API can render past dates without
        # losing the active-order data that's only true "now". Runs at
        # 02:00 Europe/Berlin so the day's final state is captured before
        # downstream tasks (audit prune at 03:00, threshold alerter at 04:00).
        "cost-report-snapshot-daily": {
            "task": "tasks.workflows.cost_report_snapshot.capture_daily_snapshot",
            "schedule": crontab(hour=2, minute=0),  # Daily at 02:00 Europe/Berlin
            "options": {"queue": "default"},
        },
        # Certification campaigns: reminders + overdue + escalation +
        # auto-revoke (each gated on its own config flag). Daily cadence
        # at 04:30 Europe/Berlin so it runs after the audit prune (03:00)
        # and the threshold alerter (04:00) have settled.
        "certification-reminder-scan": {
            "task": "tasks.workflows.certification_reminders.scan_and_remind",
            "schedule": crontab(hour=4, minute=30),  # Daily at 04:30 Europe/Berlin
            "options": {"queue": "notifications"},
        },
        # Daily check for newer ipSolis releases — opt-in via the
        # ``updates.check_enabled`` config toggle. The task short-circuits
        # itself when the toggle is off, so this Beat entry is cheap on
        # disabled installs (one DB read, no outbound call).
        "update-notifier-daily": {
            "task": "tasks.workflows.update_checker.check_for_updates",
            "schedule": crontab(hour=4, minute=30),  # Daily at 04:30 Europe/Berlin
            "options": {"queue": "default"},
        },
    },
)
