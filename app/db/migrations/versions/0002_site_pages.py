"""Site, SitePage, SitePageAudit — Phase C.1 site ingest + dashboard.

Revision ID: 0002_site_pages
Revises: 0001_initial
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_site_pages"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("root_url", sa.Text(), nullable=False),
        sa.Column("sitemap_url", sa.Text(), nullable=True),
        sa.Column("competitor_root_urls", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "root_url", name="uq_site_user_root"),
    )
    op.create_index("ix_site_user_id", "site", ["user_id"])

    op.create_table(
        "site_page",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("last_audited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_score", sa.SmallInteger(), nullable=True),
        sa.Column("last_band", sa.String(length=32), nullable=True),
        sa.Column("last_failed_checks", postgresql.JSONB(), nullable=True),
        sa.Column("last_missing_types", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("site_id", "url", name="uq_site_page_site_url"),
    )
    op.create_index("ix_site_page_site_id", "site_page", ["site_id"])

    op.create_table(
        "site_page_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "site_page_id",
            sa.BigInteger(),
            sa.ForeignKey("site_page.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("aeo_score", sa.SmallInteger(), nullable=False),
        sa.Column("band", sa.String(length=32), nullable=False),
        sa.Column("failed_checks", postgresql.JSONB(), nullable=True),
        sa.Column("missing_types", postgresql.JSONB(), nullable=True),
        sa.Column(
            "audited_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_site_page_audit_site_page_id", "site_page_audit", ["site_page_id"])
    op.create_index("ix_site_page_audit_audited_at", "site_page_audit", ["audited_at"])


def downgrade() -> None:
    op.drop_index("ix_site_page_audit_audited_at", table_name="site_page_audit")
    op.drop_index("ix_site_page_audit_site_page_id", table_name="site_page_audit")
    op.drop_table("site_page_audit")
    op.drop_index("ix_site_page_site_id", table_name="site_page")
    op.drop_table("site_page")
    op.drop_index("ix_site_user_id", table_name="site")
    op.drop_table("site")
