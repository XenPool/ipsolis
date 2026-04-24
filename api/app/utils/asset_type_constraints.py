"""AssetType Constraint Validation.

Pure function, no DB access. Called by create/update route handlers.

Mapping (spec → codebase string values):
    GROUP_ONLY     → "group_only"
    RUNBOOK_ONLY   → "runbook_only"
    COMPOSITE      → "composite"
    PERSONAL       → "assigned_personal"
    SHARED         → "dedicated_shared"
    POOLED         → "capacity_pooled"
    RETURN_TO_POOL → "return_to_pool"
    RUNBOOK (policy) → "custom_runbook"
    ACCESS_ONLY    → "access_only"
    ASSIGN_EXISTING_FREE → "assign_existing_free"

Historical notes:
- "deallocate_instance" / "delete_instance" policies were removed in
  migration 0047; they now go through custom_runbook + a dedicated
  deprovision runbook (action='delete').
- The category → capacity_pooled forced-mapping (former Rule C) was
  dropped so admins can model edge-case asset types (e.g. MDM/WLAN with
  personal 1:1 assignment) without fighting the schema.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ConstraintViolation:
    code: str
    message: str


# ── Internal constants ─────────────────────────────────────────────────────────

_GROUP_ONLY    = "group_only"
_RUNBOOK_ONLY  = "runbook_only"

_PERSONAL = "assigned_personal"

_RETURN_TO_POOL            = "return_to_pool"
_RETURN_TO_POOL_REINSTALL  = "return_to_pool_reinstall"

_ASSIGN_EXISTING_FREE = "assign_existing_free"

_POOL_RELEASE_POLICIES = {_RETURN_TO_POOL, _RETURN_TO_POOL_REINSTALL}


# ── Public validator ───────────────────────────────────────────────────────────

def validate_asset_type(
    *,
    category: str,
    assignment_model: str,
    automation_strategy: str,
    deprovision_policy: str,
    personal_provisioning_strategy: str | None,
) -> list[ConstraintViolation]:
    """Validate an AssetType payload against the core constraint rules.

    Returns a (possibly empty) list of ConstraintViolation objects. An empty
    list means the payload is valid.

    Runbook-wiring constraints (e.g. "custom_runbook policy requires a
    deprovision runbook") are NOT checked here — they're dispatch-time
    concerns that ``dynamic_runner`` enforces when an order actually runs,
    because asset types are typically created before their runbooks are
    authored, and the DB is the only place that knows which runbooks exist
    for a given asset type anyway.
    """
    errors: list[ConstraintViolation] = []

    # ── Rule A – RETURN_TO_POOL[_REINSTALL] requires PERSONAL + ASSIGN_EXISTING_FREE ──
    if deprovision_policy in _POOL_RELEASE_POLICIES:
        if assignment_model != _PERSONAL:
            errors.append(ConstraintViolation(
                code="RETURN_TO_POOL_REQUIRES_PERSONAL_ASSIGN_EXISTING_FREE",
                message=(
                    f"deprovision_policy='{deprovision_policy}' requires "
                    f"assignment_model='assigned_personal', got '{assignment_model}'."
                ),
            ))
        elif personal_provisioning_strategy != _ASSIGN_EXISTING_FREE:
            errors.append(ConstraintViolation(
                code="RETURN_TO_POOL_REQUIRES_PERSONAL_ASSIGN_EXISTING_FREE",
                message=(
                    f"deprovision_policy='{deprovision_policy}' requires "
                    f"personal_provisioning_strategy='assign_existing_free', "
                    f"got '{personal_provisioning_strategy}'."
                ),
            ))

    # ── Rule B – create_new requires runbook/composite ────────────────────────
    # Group-only automation has no script execution; create_new needs a runbook.
    _CREATE_NEW = "create_new"
    if personal_provisioning_strategy == _CREATE_NEW and automation_strategy == _GROUP_ONLY:
        errors.append(ConstraintViolation(
            code="CREATE_NEW_REQUIRES_RUNBOOK_AUTOMATION",
            message=(
                "personal_provisioning_strategy='create_new' requires a runbook to provision the VM. "
                "automation_strategy='group_only' cannot execute scripts. "
                "Use 'runbook_only' or 'composite'."
            ),
        ))

    return errors
