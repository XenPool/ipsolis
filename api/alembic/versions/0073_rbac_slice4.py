"""RBAC slice 4 — password policy + lockout + SoD per-rule opt-out.

Adds the schema needed to ship slice 4:

* ``admin_users.password_set_at`` — timestamp of the last password write,
  used by the rotation policy (``rbac.password_rotation_days``).
  Backfilled to ``created_at`` for existing rows so nobody is force-
  expired on the upgrade tick.
* ``admin_users.failed_login_count`` — counter incremented on every bad
  password attempt; reset on a successful login or admin password
  reset. Drives lockout-on-N.
* ``admin_users.locked_at`` — when set, the account is locked. The
  login flow auto-unlocks after ``rbac.lockout_duration_minutes`` so
  short bursts of bad attempts don't require admin intervention.
* ``order_approvals.sod_exempt`` — set at order-creation time when the
  rule that produced this approval row carries ``sod_exempt: true``.
  ``apply_approval_decision`` skips the SoD self-approval check when
  this flag is on (typical use: a static compliance officer who is
  also an admin and so would otherwise hit the SoD block).

Also seeds the three policy config keys with safe defaults
(``0`` rotation days = disabled, ``0`` lockout threshold = disabled,
``30`` minutes default lockout window).

Revision ID: 0073
Revises: 0072
Create Date: 2026-04-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: Union[str, None] = "0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── admin_users: password rotation + lockout columns ──────────────────
    op.add_column(
        "admin_users",
        sa.Column(
            "password_set_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "admin_users",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "admin_users",
        sa.Column(
            "locked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Backfill ``password_set_at`` to ``created_at`` so existing users
    # aren't force-expired the moment a rotation policy is enabled.
    op.execute(
        "UPDATE admin_users SET password_set_at = created_at WHERE password_set_at IS NULL"
    )

    # ── order_approvals: per-rule SoD opt-out captured at create time ─────
    op.add_column(
        "order_approvals",
        sa.Column(
            "sod_exempt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ── Default policy config (idempotent — only insert if not present) ───
    op.execute(
        """
        INSERT INTO app_config (key, value, is_secret, description)
        VALUES
          ('rbac.password_rotation_days', '0', false,
           'RBAC slice 4: force admin password change every N days. 0 disables rotation. Enterprise.'),
          ('rbac.lockout_threshold', '0', false,
           'RBAC slice 4: lock the account after N consecutive failed logins. 0 disables lockout. Enterprise.'),
          ('rbac.lockout_duration_minutes', '30', false,
           'RBAC slice 4: how long an account stays locked before auto-unlock. Enterprise.')
        ON CONFLICT (key) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app_config WHERE key IN "
        "('rbac.password_rotation_days', 'rbac.lockout_threshold', 'rbac.lockout_duration_minutes')"
    )
    op.drop_column("order_approvals", "sod_exempt")
    op.drop_column("admin_users", "locked_at")
    op.drop_column("admin_users", "failed_login_count")
    op.drop_column("admin_users", "password_set_at")
