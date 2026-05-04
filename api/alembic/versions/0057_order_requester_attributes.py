"""Capture requester AD attributes on each order for chargeback reports.

Adds six nullable columns to ``orders`` populated at order-creation time
from a configurable set of AD attributes. Existing orders stay empty
(nothing to backfill — we don't have a historical AD snapshot).

Attribute mapping is stored in ``app_config`` so tenants who park their
cost center in ``extensionAttribute1`` (or anywhere else) don't have to
patch code. Defaults match the most common AD schema: ``department`` /
``company`` / ``employeeID`` / ``title``. ``cost_center`` is left blank
by default — admins must opt in by setting the attribute name in the
Settings UI.

Revision ID: 0057
Revises: 0056
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("requester_department", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("requester_cost_center", sa.String(100), nullable=True))
    op.add_column("orders", sa.Column("requester_company", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("requester_employee_id", sa.String(50), nullable=True))
    op.add_column("orders", sa.Column("requester_sam_account", sa.String(100), nullable=True))
    op.add_column("orders", sa.Column("requester_title", sa.String(255), nullable=True))

    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        ('ad.attribute.department',
         'department',
         'AD attribute name for the user''s department. Captured on each order for chargeback reporting.',
         false, NOW()),
        ('ad.attribute.cost_center',
         '',
         'AD attribute name for the user''s cost center (commonly ''extensionAttribute1'' in enterprise schemas). Leave blank to skip.',
         false, NOW()),
        ('ad.attribute.company',
         'company',
         'AD attribute name for the user''s company / legal entity.',
         false, NOW()),
        ('ad.attribute.employee_id',
         'employeeID',
         'AD attribute name for the employee number (used for HR reconciliation).',
         false, NOW()),
        ('ad.attribute.title',
         'title',
         'AD attribute name for the user''s job title.',
         false, NOW())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
          'ad.attribute.department',
          'ad.attribute.cost_center',
          'ad.attribute.company',
          'ad.attribute.employee_id',
          'ad.attribute.title'
        )
    """)
    op.drop_column("orders", "requester_title")
    op.drop_column("orders", "requester_sam_account")
    op.drop_column("orders", "requester_employee_id")
    op.drop_column("orders", "requester_company")
    op.drop_column("orders", "requester_cost_center")
    op.drop_column("orders", "requester_department")
