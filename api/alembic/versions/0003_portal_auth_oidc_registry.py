"""Seed portal-auth toggles and migrate legacy entra.* config to the idp.* registry.

Generic OIDC SSO replaces the Entra-only MSAL path. This migration:

1. Seeds the new portal-wide auth toggles (`portal.auth_required`, `auth.ldap_enabled`).
2. Migrates any existing `entra.*` config into the generic provider registry as
   `idp.entra.*` (so test-lab deployments keep working), mapping the old
   `entra.mode` value onto the new toggles.
3. Removes the now-orphan `entra.*` keys.

Idempotent: inserts use ON CONFLICT DO NOTHING and the legacy read is a no-op once
`entra.*` rows are gone.

Revision ID: 0003
Revises: 0002
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels = None
depends_on = None

_TRUTHY = ("1", "true", "yes", "on")


def _upsert(conn, key: str, value: str, description: str, is_secret: bool) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) "
            "VALUES (:k, :v, :d, :s, NOW(), NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "is_secret = EXCLUDED.is_secret, updated_at = NOW()"
        ),
        {"k": key, "v": value, "d": description, "s": is_secret},
    )


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Read any existing entra.* config.
    rows = conn.execute(
        sa.text("SELECT key, value FROM app_config WHERE key LIKE 'entra.%'")
    ).fetchall()
    entra = {k: (v or "") for k, v in rows}

    mode = (entra.get("entra.mode") or "disabled").strip().lower()
    tenant_id = entra.get("entra.tenant_id", "").strip()
    client_id = entra.get("entra.client_id", "").strip()
    client_secret = entra.get("entra.client_secret", "").strip()
    redirect_uri = entra.get("entra.redirect_uri", "").strip()
    allowed_domains = entra.get("entra.allowed_domains", "").strip()

    had_real_config = bool(tenant_id or client_id or client_secret)

    # 2) Derive the new toggles from the old mode.
    auth_required = "true" if mode in ("entra_only", "entra_with_onprem", "onprem_ldap") else "false"
    ldap_enabled = "true" if mode in ("entra_with_onprem", "onprem_ldap") else "false"

    # Seed toggles only if absent (DO NOTHING preserves any operator value).
    conn.execute(
        sa.text(
            "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES "
            "('portal.auth_required', :ar, 'Require login to access the self-service portal (true | false)', false, NOW(), NOW()), "
            "('auth.ldap_enabled', :ld, 'Offer on-prem AD/LDAP username+password login on the portal (true | false)', false, NOW(), NOW()) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"ar": auth_required, "ld": ldap_enabled},
    )

    # 3) Migrate Entra credentials into the idp.entra.* registry (only if it was configured).
    if had_real_config:
        issuer = (
            f"https://login.microsoftonline.com/{tenant_id}/v2.0" if tenant_id else ""
        )
        enabled = "true" if mode in ("entra_only", "entra_with_onprem") else "false"
        _upsert(conn, "idp.entra.enabled", enabled, "OIDC provider enabled (true | false)", False)
        _upsert(conn, "idp.entra.display_name", "Entra ID", "Login button label", False)
        _upsert(conn, "idp.entra.issuer", issuer, "OIDC issuer URL (discovery is derived from this)", False)
        _upsert(conn, "idp.entra.client_id", client_id, "OIDC client / application id", False)
        _upsert(conn, "idp.entra.client_secret", client_secret, "OIDC client secret", True)
        _upsert(conn, "idp.entra.redirect_uri", redirect_uri, "Optional explicit redirect URI", False)
        _upsert(conn, "idp.entra.allowed_domains", allowed_domains, "Comma-separated UPN/email domain allow-list (blank = any)", False)

    # 4) Drop the orphan entra.* keys.
    conn.execute(sa.text("DELETE FROM app_config WHERE key LIKE 'entra.%'"))


def downgrade() -> None:
    conn = op.get_bind()

    # Best-effort restore of the legacy entra.* keys from idp.entra.* (issuer → tenant id).
    rows = conn.execute(
        sa.text("SELECT key, value FROM app_config WHERE key LIKE 'idp.entra.%'")
    ).fetchall()
    idp = {k: (v or "") for k, v in rows}

    issuer = idp.get("idp.entra.issuer", "")
    tenant_id = ""
    if issuer.startswith("https://login.microsoftonline.com/"):
        parts = issuer[len("https://login.microsoftonline.com/"):].split("/")
        tenant_id = parts[0] if parts else ""

    enabled = (idp.get("idp.entra.enabled", "") or "").strip().lower() in _TRUTHY
    auth_required = conn.execute(
        sa.text("SELECT value FROM app_config WHERE key = 'portal.auth_required'")
    ).scalar() or "false"
    mode = "entra_only" if (enabled and auth_required.strip().lower() in _TRUTHY) else "disabled"

    conn.execute(
        sa.text(
            "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES "
            "('entra.mode', :mode, 'Portal SSO mode: disabled | entra_only | entra_with_onprem', false, NOW(), NOW()), "
            "('entra.tenant_id', :tid, 'Azure Tenant ID (GUID)', false, NOW(), NOW()), "
            "('entra.client_id', :cid, 'App Registration Client ID (GUID)', false, NOW(), NOW()), "
            "('entra.client_secret', :sec, 'App Registration Client Secret', true, NOW(), NOW()), "
            "('entra.redirect_uri', :ru, 'OAuth2 callback URL (must match App Registration)', false, NOW(), NOW()), "
            "('entra.allowed_domains', :ad, 'Comma-separated UPN suffixes allowed to log in (blank = any)', false, NOW(), NOW()) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {
            "mode": mode,
            "tid": tenant_id,
            "cid": idp.get("idp.entra.client_id", ""),
            "sec": idp.get("idp.entra.client_secret", ""),
            "ru": idp.get("idp.entra.redirect_uri", ""),
            "ad": idp.get("idp.entra.allowed_domains", ""),
        },
    )

    # Remove the registry + toggles introduced by this migration.
    conn.execute(sa.text("DELETE FROM app_config WHERE key LIKE 'idp.%'"))
    conn.execute(
        sa.text("DELETE FROM app_config WHERE key IN ('portal.auth_required', 'auth.ldap_enabled')")
    )
