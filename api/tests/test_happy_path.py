"""Happy-path tests.

Run from the api/ directory:
    python -m pytest tests/test_happy_path.py -v

Coverage
--------
1. _final_status  – correct terminal status for each order action
2. _render_params – template variable substitution in runbook params
3. Runbook lookup – _run_runbook_path finds / refuses the correct runbook
   (runbook row found with no steps → order marked provisioned / delivered)
4. Targets mode   – group_only PROVISION and DELETE happy paths
"""

from collections import namedtuple
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# conftest.pytest_configure adds worker/ to sys.path before this module loads.
# All tasks.* imports resolve to the real worker source tree.
from tasks.workflows.dynamic_runner import (
    _final_status,
    _render_params,
    _run_runbook_path,
    _run_targets_mode,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

RunbookRow = namedtuple("RunbookRow", ["id", "name", "is_active"])


def _sample_order(asset_type_id: int = 1, action: str = "provision") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": 42,
        "asset_type_id": asset_type_id,
        "action": action,
        "user_email": "test@xenpool.de",
        "user_name": "Test User",
        "owner_email": None,
        "owner_name": None,
        "rdp_users": [],
        "admin_users": [],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=30)).isoformat(),
        "snow_req": None,
        "servicenow_ref": None,
        "assigned_asset_id": None,
    }


def _celery_task() -> MagicMock:
    t = MagicMock()
    t.request.id = "test-task-id"
    return t


def _mock_db(runbook_row=None, step_rows=None) -> MagicMock:
    """Sync DB mock for _run_runbook_path.

    execute() call sequence:
      0 → runbook query    → .fetchone()  returns runbook_row
      1 → steps query      → .fetchall() returns step_rows
      2+ → any other query → returns generic MagicMock
    """
    db = MagicMock()
    seq = [0]

    def _side(*_a, **_kw):
        r = MagicMock()
        idx = seq[0]
        seq[0] += 1
        if idx == 0:
            r.fetchone.return_value = runbook_row
        elif idx == 1:
            r.fetchall.return_value = step_rows if step_rows is not None else []
        return r

    db.execute.side_effect = _side
    return db


# ═════════════════════════════════════════════════════════════════════════════
# 1. _final_status
# ═════════════════════════════════════════════════════════════════════════════

class TestFinalStatus:
    def test_provision_returns_provisioned(self):
        assert _final_status("provision") == "provisioned"

    def test_delete_returns_revoked(self):
        assert _final_status("delete") == "revoked"

    def test_modify_returns_delivered(self):
        assert _final_status("modify") == "delivered"

    def test_extend_returns_delivered(self):
        assert _final_status("extend") == "delivered"


# ═════════════════════════════════════════════════════════════════════════════
# 2. _render_params
# ═════════════════════════════════════════════════════════════════════════════

class TestRenderParams:
    def test_single_substitution(self):
        result = _render_params({"Host": "{{host}}"}, {"host": "srv1.xenpool.local"})
        assert result == {"Host": "srv1.xenpool.local"}

    def test_missing_context_key_yields_none(self):
        result = _render_params({"X": "{{does_not_exist}}"}, {})
        assert result["X"] is None

    def test_literal_passes_through(self):
        result = _render_params({"Key": "not-a-template"}, {})
        assert result["Key"] == "not-a-template"

    def test_multiple_keys(self):
        result = _render_params(
            {"A": "{{a}}", "B": "{{b}}", "C": "static"},
            {"a": "alpha", "b": "beta"},
        )
        assert result == {"A": "alpha", "B": "beta", "C": "static"}


# ═════════════════════════════════════════════════════════════════════════════
# 3. Runbook lookup  (_run_runbook_path)
#
# Patch at the dynamic_runner namespace because the module-level import
# `from tasks.modules.step_helper import update_order_status` creates a
# local binding; patching the step_helper module would not affect it.
# ═════════════════════════════════════════════════════════════════════════════

_DR = "tasks.workflows.dynamic_runner"  # patch target prefix


class TestRunbookLookup:

    @patch(f"{_DR}.update_order_step")
    @patch(f"{_DR}.update_order_status")
    def test_runbook_found_empty_steps_marks_provisioned(self, mock_status, mock_step):
        """Runbook with zero steps → order lands on 'provisioned' (happy path)."""
        db = _mock_db(
            runbook_row=RunbookRow(id=1, name="VDI Provision", is_active=True),
            step_rows=[],
        )
        result = _run_runbook_path(
            celery_task=_celery_task(),
            db=db,
            order_id=42,
            order=_sample_order(),
            action="provision",
            asset_type_name="VDI Standard",
            asset_type_description="",
            assignment_model="capacity_pooled",
        )

        assert result["success"] is True
        mock_status.assert_called_with(db, 42, "provisioned")

    @patch(f"{_DR}.update_order_status")
    def test_runbook_not_found_returns_failure(self, mock_status):
        """No matching runbook for asset_type + action → success=False."""
        db = _mock_db(runbook_row=None)

        result = _run_runbook_path(
            celery_task=_celery_task(),
            db=db,
            order_id=42,
            order=_sample_order(action="delete"),
            action="delete",
            asset_type_name="VDI Standard",
            asset_type_description="",
        )

        assert result["success"] is False
        assert "No runbook found" in result["error"]

    @patch(f"{_DR}.update_order_status")
    def test_inactive_runbook_refused(self, mock_status):
        """Runbook with is_active=False must not execute."""
        db = _mock_db(
            runbook_row=RunbookRow(id=5, name="Disabled Runbook", is_active=False),
        )

        result = _run_runbook_path(
            celery_task=_celery_task(),
            db=db,
            order_id=42,
            order=_sample_order(),
            action="provision",
            asset_type_name="VDI Standard",
            asset_type_description="",
        )

        assert result["success"] is False
        assert "disabled" in result["error"]

    @patch(f"{_DR}.update_order_status")
    def test_modify_without_runbook_is_noop(self, mock_status):
        """modify / extend with no runbook → treated as success (no-op)."""
        db = _mock_db(runbook_row=None)

        result = _run_runbook_path(
            celery_task=_celery_task(),
            db=db,
            order_id=42,
            order=_sample_order(action="modify"),
            action="modify",
            asset_type_name="VDI Standard",
            asset_type_description="",
        )

        assert result["success"] is True
        assert result.get("skipped") is True


# ═════════════════════════════════════════════════════════════════════════════
# 4. Happy path: group_only PROVISION and DELETE
#
# _run_targets_mode does local imports (`from tasks.modules import ...`),
# so we patch at the module attribute level (not in dynamic_runner namespace).
# ═════════════════════════════════════════════════════════════════════════════

_NOTIF = "tasks.modules.notifications"
_TE    = "tasks.modules.target_executor"
_AUDIT = "tasks.modules.audit_helper"


class TestTargetsModeHappyPath:

    @patch(f"{_AUDIT}.waudit")
    @patch(f"{_TE}.grant", return_value={"success": True})
    @patch(f"{_NOTIF}.send_order_confirmation", return_value={"success": True})
    @patch(f"{_DR}.update_order_step")
    @patch(f"{_DR}.update_order_status")
    def test_provision_group_only_capacity_pooled(
        self, mock_status, mock_step, mock_notif, mock_grant, mock_audit
    ):
        """
        group_only + capacity_pooled PROVISION:
        - Order confirmation email sent (non-critical)
        - Grant access called once
        - No asset reservation needed
        - Final status = "provisioned"
        """
        db = MagicMock()

        result = _run_targets_mode(
            celery_task=_celery_task(),
            db=db,
            order_id=42,
            order=_sample_order(action="provision"),
            action="provision",
            asset_type_name="VDI Standard",
            asset_type_description="Test VDI type",
            assignment_model="capacity_pooled",
            deprovision_policy="access_only",
            automation_strategy="group_only",
        )

        assert result["success"] is True
        assert result["order_id"] == 42
        mock_notif.assert_called_once()
        mock_grant.assert_called_once()
        mock_status.assert_called_with(db, 42, "provisioned")

    @patch(f"{_AUDIT}.waudit")
    @patch(f"{_TE}.revoke", return_value={"success": True})
    @patch(f"{_DR}.update_order_step")
    @patch(f"{_DR}.update_order_status")
    def test_delete_group_only_access_only(
        self, mock_status, mock_step, mock_revoke, mock_audit
    ):
        """
        group_only + access_only DELETE:
        - Revoke access called once
        - Final status = "revoked"
        """
        db = MagicMock()

        result = _run_targets_mode(
            celery_task=_celery_task(),
            db=db,
            order_id=42,
            order=_sample_order(action="delete"),
            action="delete",
            asset_type_name="VDI Standard",
            asset_type_description="",
            assignment_model="capacity_pooled",
            deprovision_policy="access_only",
            automation_strategy="group_only",
        )

        assert result["success"] is True
        mock_revoke.assert_called_once()
        mock_status.assert_called_with(db, 42, "revoked")
