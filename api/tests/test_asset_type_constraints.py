"""Unit tests for AssetType create-time constraint validation.

The current ``validate_asset_type()`` enforces only the two *create-time* rules.
The legacy runbook-wiring constraints (e.g. "custom_runbook policy requires a
deprovision runbook") and the old ``deallocate`` / ``delete`` / ``*_instance``
deprovision policies were removed in the migration-0047 refactor — runbook
wiring is now a dispatch-time concern enforced by ``dynamic_runner`` when an
order actually runs (the DB is the only place that knows which runbooks exist
for an asset type). This suite tracks the validator as it is today:

  Rule A  deprovision_policy in {return_to_pool, return_to_pool_reinstall}
          → requires assignment_model=assigned_personal AND
            personal_provisioning_strategy=assign_existing_free
          (code RETURN_TO_POOL_REQUIRES_PERSONAL_ASSIGN_EXISTING_FREE)
  Rule B  personal_provisioning_strategy=create_new AND
          automation_strategy=group_only
          (code CREATE_NEW_REQUIRES_RUNBOOK_AUTOMATION)

Pure function — no DB or framework required.

Run with:
    cd api && python -m pytest tests/test_asset_type_constraints.py -v
"""
from app.utils.asset_type_constraints import validate_asset_type


def _codes(violations) -> set[str]:
    return {v.code for v in violations}


def _validate(**kwargs):
    """Call validate_asset_type with sensible defaults; override per test."""
    defaults = dict(
        category="platform_access",
        assignment_model="assigned_personal",
        automation_strategy="runbook_only",
        deprovision_policy="access_only",
        personal_provisioning_strategy=None,
    )
    defaults.update(kwargs)
    return validate_asset_type(**defaults)


# ── Valid combinations ───────────────────────────────────────────────────────

class TestMustPass:
    def test_pool_group_only_access_only(self):
        assert _validate(
            assignment_model="capacity_pooled",
            automation_strategy="group_only",
            deprovision_policy="access_only",
        ) == []

    def test_personal_return_to_pool_with_assign_existing_free(self):
        assert _validate(
            deprovision_policy="return_to_pool",
            personal_provisioning_strategy="assign_existing_free",
        ) == []

    def test_personal_return_to_pool_reinstall_with_assign_existing_free(self):
        assert _validate(
            deprovision_policy="return_to_pool_reinstall",
            personal_provisioning_strategy="assign_existing_free",
        ) == []

    def test_create_new_with_runbook_automation(self):
        assert _validate(
            automation_strategy="runbook_only",
            personal_provisioning_strategy="create_new",
        ) == []

    def test_create_new_with_composite_automation(self):
        assert _validate(
            automation_strategy="composite",
            personal_provisioning_strategy="create_new",
        ) == []

    def test_custom_runbook_policy_is_create_time_valid(self):
        # Runbook wiring (provision/revoke runbook present) is enforced at
        # dispatch time, not here — so custom_runbook alone is valid at create.
        assert _validate(
            automation_strategy="runbook_only",
            deprovision_policy="custom_runbook",
        ) == []


# ── Rule A: return_to_pool[_reinstall] ───────────────────────────────────────

class TestReturnToPoolRule:
    CODE = "RETURN_TO_POOL_REQUIRES_PERSONAL_ASSIGN_EXISTING_FREE"

    def test_non_personal_fails(self):
        assert self.CODE in _codes(_validate(
            assignment_model="capacity_pooled",
            deprovision_policy="return_to_pool",
        ))

    def test_reinstall_non_personal_fails(self):
        assert self.CODE in _codes(_validate(
            assignment_model="capacity_pooled",
            deprovision_policy="return_to_pool_reinstall",
        ))

    def test_personal_wrong_strategy_fails(self):
        assert self.CODE in _codes(_validate(
            deprovision_policy="return_to_pool",
            personal_provisioning_strategy="create_new",
        ))

    def test_personal_missing_strategy_fails(self):
        # personal_provisioning_strategy=None != assign_existing_free
        assert self.CODE in _codes(_validate(
            deprovision_policy="return_to_pool",
            personal_provisioning_strategy=None,
        ))


# ── Rule B: create_new + group_only ──────────────────────────────────────────

class TestCreateNewRule:
    CODE = "CREATE_NEW_REQUIRES_RUNBOOK_AUTOMATION"

    def test_create_new_group_only_fails(self):
        assert self.CODE in _codes(_validate(
            automation_strategy="group_only",
            personal_provisioning_strategy="create_new",
        ))

    def test_create_new_runbook_only_ok(self):
        assert _validate(
            automation_strategy="runbook_only",
            personal_provisioning_strategy="create_new",
        ) == []


# ── Both rules at once ───────────────────────────────────────────────────────

class TestMultipleViolations:
    def test_both_rules_fire(self):
        # return_to_pool on a personal + create_new + group_only definition:
        #   Rule A — create_new != assign_existing_free, and
        #   Rule B — create_new + group_only.
        errors = _validate(
            assignment_model="assigned_personal",
            automation_strategy="group_only",
            deprovision_policy="return_to_pool",
            personal_provisioning_strategy="create_new",
        )
        codes = _codes(errors)
        assert "RETURN_TO_POOL_REQUIRES_PERSONAL_ASSIGN_EXISTING_FREE" in codes
        assert "CREATE_NEW_REQUIRES_RUNBOOK_AUTOMATION" in codes
        assert len(errors) == 2
