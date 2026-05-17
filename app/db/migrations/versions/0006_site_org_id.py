"""Site.org_id — Critical Decision #4 multi-tenancy bridge.

Revision ID: 0006_site_org_id
Revises: 0005_api_webhooks
Create Date: 2026-05-17

Additive only: adds nullable `org_id` + index. No backfill — existing
rows keep their `user_id` ownership, and new ingests record both. Comment
authorisation projection prefers `org_id` when present.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_site_org_id"
down_revision = "0005_api_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "site",
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("org.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_site_org_id", "site", ["org_id"])
    # Tightening up the existing per-user uniqueness: a single org should
    # only ever ingest a root_url once. We can't enforce a single UNIQUE
    # over `(org_id, root_url)` while preserving the legacy
    # `(user_id, root_url)` rule simultaneously without partial indexes,
    # so add a partial index gated on `org_id IS NOT NULL`.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_site_org_root "
        "ON site (org_id, root_url) WHERE org_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_site_org_root")
    op.drop_index("ix_site_org_id", table_name="site")
    op.drop_column("site", "org_id")
