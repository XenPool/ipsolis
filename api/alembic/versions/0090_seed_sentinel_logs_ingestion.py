"""Seed Sentinel Logs Ingestion API config keys.

Microsoft is sunsetting the legacy Log Analytics **Data Collector**
API (the current ``siem.format = 'sentinel'`` path) on **31 August
2026**. The replacement is the **Logs Ingestion API** — a Data
Collection Endpoint (DCE) + Data Collection Rule (DCR) + named
stream, signed with an AAD bearer token on a Service Principal
granted the **Monitoring Metrics Publisher** role on the DCR.

This migration adds five new keys for that path. The existing
Sentinel keys (``workspace_id``, ``shared_key``, ``log_type``)
stay around so installs already on the Data Collector API continue
to work — operators flip ``siem.format`` to ``sentinel_log_ingestion``
when they finish provisioning their DCE/DCR. Both formats can be
configured side-by-side; only the active ``siem.format`` value
drives streaming.

Why a separate SPN from the Azure KV one? Same reason the KV SPN
is separate from the Entra ID SSO SPN: minimum-necessary access.
The Sentinel SPN gets ``Monitoring Metrics Publisher`` on the DCR
and nothing else; Azure KV's gets Key Vault Secrets User on the
vault; Entra ID SSO's gets ``User.Read`` delegated. Cross-mixing
escalates the blast radius of a compromised secret.

Revision ID: 0090
Revises: 0089
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0090"
down_revision: Union[str, None] = "0089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KEYS = [
    (
        "siem.sentinel_dce_endpoint",
        "",
        "Sentinel Logs Ingestion API: Data Collection Endpoint URL "
        "(e.g. 'https://ipsolis-dce-xxxx.westeurope-1.ingest.monitor.azure.com'). "
        "Read off the DCE's overview blade in Azure Portal. No trailing slash.",
        False,
    ),
    (
        "siem.sentinel_dcr_immutable_id",
        "",
        "Sentinel Logs Ingestion API: Data Collection Rule immutable id "
        "(e.g. 'dcr-abcd1234ef567890'). Found on the DCR's JSON view in "
        "Azure Portal under properties.immutableId — NOT the resource id "
        "or the DCR's friendly name.",
        False,
    ),
    (
        "siem.sentinel_stream_name",
        "Custom-IpsolisAudit_CL",
        "Sentinel Logs Ingestion API: stream name declared on the DCR "
        "(e.g. 'Custom-IpsolisAudit_CL'). The leading 'Custom-' prefix "
        "is required for custom-table streams; the '_CL' suffix is the "
        "Log Analytics convention for custom log tables.",
        False,
    ),
    (
        "siem.sentinel_tenant_id",
        "",
        "Sentinel Logs Ingestion API: Azure AD tenant id (GUID) hosting "
        "the SPN. Independent from secret.azurekv.tenant_id and "
        "entra.tenant_id even when they're the same value — keeps the "
        "audit-streaming SPN's role assignment narrow ('Monitoring "
        "Metrics Publisher' on the DCR only).",
        False,
    ),
    (
        "siem.sentinel_client_id",
        "",
        "Sentinel Logs Ingestion API: Application (client) id of the SPN.",
        False,
    ),
    (
        "siem.sentinel_client_secret",
        "",
        "Sentinel Logs Ingestion API: client secret for the SPN. Stored "
        "as a secret (masked in admin UI). Rotate from the Azure side "
        "and paste the new value here.",
        True,
    ),
]


def upgrade() -> None:
    for key, value, description, is_secret in _KEYS:
        op.execute(
            f"""
            INSERT INTO app_config (key, value, description, is_secret)
            VALUES ({_lit(key)}, {_lit(value)}, {_lit(description)}, {str(is_secret).lower()})
            ON CONFLICT (key) DO NOTHING
            """
        )

    # Bump the format key's description so admins see the new option in
    # the live-config inspector even before they touch the Settings UI.
    op.execute(
        """
        UPDATE app_config
        SET description = 'SIEM payload format. One of: splunk_hec, '
                          'sentinel (legacy Data Collector API — '
                          'sunset 2026-08-31), sentinel_log_ingestion '
                          '(Logs Ingestion API — recommended replacement '
                          'for Sentinel), or webhook.'
        WHERE key = 'siem.format'
        """
    )


def downgrade() -> None:
    for key, _, _, _ in _KEYS:
        op.execute(f"DELETE FROM app_config WHERE key = {_lit(key)}")


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
