"""Seed config keys driving the one-shot plaintext→backend migration tool.

Two keys:

* ``secret.migration_prefix`` — common name prefix the migration tool
  prepends to every key it pushes to the backend. Default ``ipsolis``
  so a fresh tenant doesn't collide with whatever else lives at the
  store root. Operators with multiple ipSolis tenants behind one
  shared backend should set this per tenant (e.g. ``ipsolis/prod``,
  ``ipsolis/lab``) so references don't cross-pollinate.

* ``secret.azurekv.migration_vault`` — Azure KV references carry the
  vault name in the reference itself (``azurekv://<vault>/<name>``),
  so the migration tool needs to know which vault to write to. No
  default — admins must set it before migrating to Azure KV. Other
  backends derive their address space entirely from
  ``secret.migration_prefix``.

Revision ID: 0087
Revises: 0086
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0087"
down_revision: Union[str, None] = "0086"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KEYS = [
    (
        "secret.migration_prefix",
        "ipsolis",
        "One-shot migration tool: name prefix prepended to every key "
        "pushed to the backend. Default 'ipsolis' avoids collisions "
        "with non-ipSolis content at the store root. Set per tenant "
        "(e.g. 'ipsolis/prod') when multiple ipSolis instances share a backend.",
        False,
    ),
    (
        "secret.azurekv.migration_vault",
        "",
        "One-shot migration tool: Azure Key Vault name to write into "
        "(e.g. 'kv-prod-ipsolis'). Required before migrating to Azure KV "
        "since the vault name lives in the reference itself "
        "(azurekv://<vault>/<name>). Other backends derive their address "
        "space from secret.migration_prefix.",
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


def downgrade() -> None:
    for key, _, _, _ in _KEYS:
        op.execute(f"DELETE FROM app_config WHERE key = {_lit(key)}")


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
