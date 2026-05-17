"""GEOQuery + GEOProbeRecord — Phase C.2 GEO citation tracking.

Revision ID: 0003_geo
Revises: 0002_site_pages
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_geo"
down_revision = "0002_site_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geo_query",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_query", sa.String(length=512), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="en"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_contains_site", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "site_id", "target_query", "locale", name="uq_geo_query_site_query_locale"
        ),
    )
    op.create_index("ix_geo_query_site_id", "geo_query", ["site_id"])

    op.create_table(
        "geo_probe",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "geo_query_id",
            sa.BigInteger(),
            sa.ForeignKey("geo_query.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("cited_urls", postgresql.JSONB(), nullable=False),
        sa.Column("contains_site", sa.Boolean(), nullable=False),
        sa.Column("site_position", sa.SmallInteger(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column(
            "probed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_geo_probe_geo_query_id", "geo_probe", ["geo_query_id"])
    op.create_index("ix_geo_probe_probed_at", "geo_probe", ["probed_at"])


def downgrade() -> None:
    op.drop_index("ix_geo_probe_probed_at", table_name="geo_probe")
    op.drop_index("ix_geo_probe_geo_query_id", table_name="geo_probe")
    op.drop_table("geo_probe")
    op.drop_index("ix_geo_query_site_id", table_name="geo_query")
    op.drop_table("geo_query")
