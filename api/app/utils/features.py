"""Feature flag registry and FastAPI dependencies for Business/Enterprise gating.

Feature keys and tier assignments come from ``EDITIONS.md``. Any
change to that document should be mirrored here.

Tier hierarchy: Enterprise ⊇ Business ⊇ Community.
- Use ``require_business(key)`` for features available on Business + Enterprise.
- Use ``require_enterprise(key)`` for features that require Enterprise only.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.utils.license import is_feature_enabled

# Features available on Business and Enterprise licenses.
BUSINESS_FEATURES: dict[str, str] = {
    "standalone_runbooks":      "Standalone Runbooks",
    "visual_runbook_builder":   "Visual Runbook Builder",
    "ps_module_management":     "PowerShell Module Management",
    "deputy_support":           "Deputy Support",
    "scheduled_orders":         "Scheduled Orders",
    "app_owner_approval":       "Application Owner Approval",
    "reapproval_on_modify":     "Re-approval on Modify",
    "email_template_editor":    "Email Template Editor",
    "app_branding":             "App Branding",
    "eligible_requestors":      "Eligible Requestors",
    "global_variables":         "Global Variables",
    "audit_log_viewer":         "Audit Log Viewer",
    "change_log_viewer":        "Order Change Log Viewer",
    "api_token_management":     "Per-Integration API Tokens",
    "certifications":           "Access Certification Campaigns",
}

# Features that require an Enterprise license (hard-blocked on Business).
ENTERPRISE_FEATURES: dict[str, str] = {
    "servicenow_webhook":       "ServiceNow Webhook",
    "hr_webhook":               "HR Leaver Webhook",
    "hr_leaver_events":         "HR Leaver Events Viewer",
    "scim":                     "SCIM 2.0 Provisioning",
    "vsphere_integration":      "VMware vSphere Integration",
    "xenserver_integration":    "XenServer / XCP-ng Integration",
    "sccm_integration":         "SCCM Integration",
    # Per-classification audit retention (PII / PHI / PCI 7+ yr windows).
    "audit_retention":          "Audit Log Retention Policy",
    "advanced_maintenance":     "Advanced Maintenance",
    "custom_deprovision":       "Custom Deprovision Policy",
    # RBAC compliance extensions (auditor-grade, regulated environments).
    "rbac_asset_type_grants":   "Per-Asset-Type ACL Grants",
    "rbac_token_role_binding":  "Role-Bound API Tokens",
    "rbac_sod_enforcement":     "Separation-of-Duties Enforcement",
    "password_policy":          "Password Rotation & Lockout Policy",
}

# Combined lookup used by error helpers.
ALL_GATED_FEATURES: dict[str, str] = {**BUSINESS_FEATURES, **ENTERPRISE_FEATURES}


def _business_error(feature: str) -> HTTPException:
    label = ALL_GATED_FEATURES.get(feature, feature)
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"{label} requires an ip·Solis Business or Enterprise license. "
            f"Contact info@xenpool.com for licensing options."
        ),
    )


def _enterprise_error(feature: str) -> HTTPException:
    label = ALL_GATED_FEATURES.get(feature, feature)
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"{label} requires an ip·Solis Enterprise license. "
            f"Contact info@xenpool.com for licensing options."
        ),
    )


def require_business(feature: str):
    """FastAPI dependency factory. Raises 403 if feature requires Business/Enterprise.

    Use for features in BUSINESS_FEATURES. Enterprise licenses also satisfy this gate.

    Usage:
        @router.get("/thing", dependencies=[require_business("standalone_runbooks")])
        async def endpoint(): ...
    """
    async def _check() -> None:
        if not is_feature_enabled(feature):
            raise _business_error(feature)

    return Depends(_check)


def require_enterprise(feature: str):
    """FastAPI dependency factory. Raises 403 if the feature is not Enterprise-licensed.

    Use for features in ENTERPRISE_FEATURES. Business licenses are rejected.

    Usage:
        @router.get("/thing", dependencies=[require_enterprise("scim")])
        async def endpoint(): ...

        # or per-router
        router = APIRouter(
            prefix="/admin/scim",
            dependencies=[Depends(require_admin_key), require_enterprise("scim")],
        )
    """
    async def _check() -> None:
        if not is_feature_enabled(feature):
            raise _enterprise_error(feature)

    return Depends(_check)
