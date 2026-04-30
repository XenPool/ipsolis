"""Seed Vault auth-method config keys (AppRole + Kubernetes JWT).

Slice-1 of the Vault adapter shipped static-token auth only — the
operator pasted a long-lived token into ``secret.vault.token`` and
ip·Solis sent it on every read. Static tokens are operationally
brittle (they don't auto-renew, rotation requires a config edit
plus a restart-all-the-resolvers, and Vault Enterprise governance
generally bans them outside lab use).

This migration adds two production-grade auth methods alongside the
existing static-token path:

* **AppRole** — a role_id (identity) + secret_id (credential) pair
  swapped at ``/v1/auth/approle/login`` for a short-lived token.
  Standard for non-Kubernetes workloads. The secret_id is the only
  high-value credential (role_id is essentially public).
* **Kubernetes JWT** — the pod's projected service-account JWT
  swapped at ``/v1/auth/kubernetes/login`` for a token. Standard
  for in-cluster workloads — no long-lived secret on disk.

The active method is picked via ``secret.vault.auth_method`` (one
of ``token`` / ``approle`` / ``kubernetes``), defaulting to
``token`` so existing installs upgrade silently.

Revision ID: 0088
Revises: 0087
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0088"
down_revision: Union[str, None] = "0087"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KEYS = [
    (
        "secret.vault.auth_method",
        "token",
        "Vault authentication method. One of: 'token' (static — slice 1, "
        "default), 'approle' (role_id + secret_id swap, recommended for "
        "non-Kubernetes workloads), or 'kubernetes' (projected service-"
        "account JWT swap, recommended for in-cluster workloads). Token "
        "method ignores the AppRole / k8s keys; the others ignore "
        "secret.vault.token.",
        False,
    ),
    (
        "secret.vault.approle_path",
        "approle",
        "AppRole mount path, default 'approle'. Change only if your "
        "Vault admin enabled the AppRole auth method at a non-default "
        "path (e.g. 'auth/approle-prod' → set to 'approle-prod').",
        False,
    ),
    (
        "secret.vault.approle_role_id",
        "",
        "AppRole role_id — the role identity, treated as public-ish "
        "(it identifies the app but isn't itself a credential). "
        "Pair with secret.vault.approle_secret_id for login.",
        False,
    ),
    (
        "secret.vault.approle_secret_id",
        "",
        "AppRole secret_id — the actual credential paired with role_id "
        "to mint a Vault token. Stored as a secret (masked in admin UI). "
        "Rotate from the Vault side and paste the new value here. "
        "Vault wraps secret_ids by default for transport via response-"
        "wrapping; ip·Solis expects an unwrapped secret_id.",
        True,
    ),
    (
        "secret.vault.k8s_path",
        "kubernetes",
        "Kubernetes auth mount path, default 'kubernetes'. Change only "
        "if your Vault admin enabled the Kubernetes auth method at a "
        "non-default path (e.g. 'auth/k8s-prod' → set to 'k8s-prod').",
        False,
    ),
    (
        "secret.vault.k8s_role",
        "",
        "Kubernetes auth role name (the role bound on the Vault side via "
        "'vault write auth/kubernetes/role/<name> ...'). Determines the "
        "policies attached to the minted token.",
        False,
    ),
    (
        "secret.vault.k8s_jwt_path",
        "/var/run/secrets/kubernetes.io/serviceaccount/token",
        "Path to the projected service-account JWT inside the API / "
        "worker container. The default matches the standard Kubernetes "
        "ProjectedVolume mount; override for sidecar / custom volume "
        "layouts. The file is read fresh on every login attempt so "
        "kubelet rotation lands without a restart.",
        False,
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

    # Refresh the static-token description so admins know it's only
    # consulted when auth_method = 'token'.
    op.execute(
        """
        UPDATE app_config
        SET description = 'Vault static token (X-Vault-Token). Used only when '
                          'secret.vault.auth_method = ''token''. Long-lived '
                          'tokens are operationally brittle — production '
                          'installs should switch to AppRole or Kubernetes '
                          'JWT and leave this empty.'
        WHERE key = 'secret.vault.token'
        """
    )


def downgrade() -> None:
    for key, _, _, _ in _KEYS:
        op.execute(f"DELETE FROM app_config WHERE key = {_lit(key)}")
    op.execute(
        """
        UPDATE app_config
        SET description = 'Vault token (X-Vault-Token).'
        WHERE key = 'secret.vault.token'
        """
    )


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
