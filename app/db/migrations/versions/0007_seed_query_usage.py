"""seed_query_usage — autopilot seed rotation state.

Revision ID: 0007_seed_query_usage
Revises: 0006_site_org_id
Create Date: 2026-05-17

Tracks per-slug last-used timestamp + use count so the autopilot can
rotate fairly through `app.autopilot.seed_queries.SEED_QUERIES` across
cron ticks without an operator having to pass `--seed` every time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_seed_query_usage"
down_revision = "0006_site_org_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seed_query_usage",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_table("seed_query_usage")
