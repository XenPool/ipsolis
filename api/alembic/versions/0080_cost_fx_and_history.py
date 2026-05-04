"""Cost report — FX conversion + historical snapshots.

Adds two complementary capabilities to the cost / chargeback feature:

1. **FX conversion** (config-only): static currency-conversion rates in
   ``app_config`` + a canonical reporting currency. The cost report
   accepts ``?reporting_currency=`` and converts mixed-currency totals
   on the fly. No per-order rate snapshot — these are admin-supplied
   reporting rates, not transaction rates, so changing them re-renders
   historical projections in the new view too. Set rate to 1.00 for the
   canonical currency itself; leave other currencies blank to disable
   conversion (the report falls back to the per-currency view).

2. **Historical view**: a daily Beat task captures the full provider /
   consumer-cost-center / consumer-department aggregations into
   ``cost_report_snapshots``, keyed by snapshot date + view + dimension.
   The cost report then accepts ``?as_of=YYYY-MM-DD`` to render the
   snapshot at that date instead of the live "currently active"
   computation. Useful for trend-tracking and end-of-month chargeback.

Revision ID: 0080
Revises: 0079
Create Date: 2026-04-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0080"
down_revision: Union[str, None] = "0079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── FX config ───────────────────────────────────────────────────────────
    op.execute(
        """
        INSERT INTO app_config (key, value, description, is_secret)
        VALUES (
            'cost.fx.canonical',
            'EUR',
            'Canonical reporting currency for FX conversion. The cost report converts mixed-currency totals into this currency when the user selects "Show in <canonical>". ISO 4217 code, uppercased.',
            false
        )
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_config (key, value, description, is_secret)
        VALUES (
            'cost.fx.rates',
            '{}',
            'JSON object mapping ISO 4217 currency code to the conversion rate INTO the canonical currency (e.g. {"USD": 0.92} means 1 USD = 0.92 EUR when canonical=EUR). Set 1.00 for the canonical currency. Missing currencies fall through to the per-currency view.',
            false
        )
        ON CONFLICT (key) DO NOTHING
        """
    )

    # ── Snapshot table ──────────────────────────────────────────────────────
    # Composite PK on (snapshot_date, view, dimension_key, currency) so the
    # same dimension can hold per-currency rows and we don't double-count
    # on the aggregate views. ``view`` is one of:
    #   - 'provider'      — asset definition's cost_center
    #   - 'consumer_cc'   — requester's AD cost_center
    #   - 'consumer_dept' — requester's AD department
    op.create_table(
        "cost_report_snapshots",
        sa.Column("snapshot_date", sa.Date(), primary_key=True),
        sa.Column("view", sa.String(length=20), primary_key=True),
        sa.Column("dimension_key", sa.String(length=255), primary_key=True),
        sa.Column("currency", sa.String(length=3), primary_key=True),
        sa.Column("projected_monthly_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("active_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("asset_types", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Reverse-lookup index for the date-range / dimension queries the UI fires.
    op.create_index(
        "ix_cost_report_snapshots_view_date",
        "cost_report_snapshots",
        ["view", "snapshot_date"],
    )

    # Snapshot retention — daily captures over years gets large; default 365
    # days is enough for year-over-year comparisons. 0 = keep forever.
    op.execute(
        """
        INSERT INTO app_config (key, value, description, is_secret)
        VALUES (
            'cost.snapshot_retention_days',
            '365',
            'How many days of cost_report_snapshots to retain. The daily snapshot Beat task prunes rows older than this. 0 = keep forever (storage grows ~3 rows/day per cost-center/currency combination).',
            false
        )
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key IN ('cost.fx.canonical','cost.fx.rates','cost.snapshot_retention_days')")
    op.drop_index("ix_cost_report_snapshots_view_date", table_name="cost_report_snapshots")
    op.drop_table("cost_report_snapshots")
