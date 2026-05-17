"""Org / OrgMember / AuditComment — Phase D collaboration + permissions.

Revision ID: 0004_org_comments
Revises: 0003_geo
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_org_comments"
down_revision = "0003_geo"
branch_labels = None
depends_on = None


_E = dict(create_type=False)

ORG_ROLE = postgresql.ENUM("owner", "editor", "viewer", name="org_role", **_E)


def upgrade() -> None:
    bind = op.get_bind()
    ORG_ROLE.create(bind, checkfirst=True)

    op.create_table(
        "org",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("branded_subdomain", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_org_slug"),
        sa.UniqueConstraint("branded_subdomain", name="uq_org_branded_subdomain"),
    )
    op.create_index("ix_org_slug", "org", ["slug"])

    op.create_table(
        "org_member",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("org.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", ORG_ROLE, nullable=False),
        sa.Column("invited_email", postgresql.CITEXT(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("org_id", "user_id", name="uq_org_member_org_user"),
    )
    op.create_index("ix_org_member_org_id", "org_member", ["org_id"])
    op.create_index("ix_org_member_user_id", "org_member", ["user_id"])

    op.create_table(
        "audit_comment",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "site_page_audit_id",
            sa.BigInteger(),
            sa.ForeignKey("site_page_audit.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("check_id", sa.String(length=64), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_comment_site_page_audit_id", "audit_comment", ["site_page_audit_id"])
    op.create_index("ix_audit_comment_author_user_id", "audit_comment", ["author_user_id"])
    op.create_index("ix_audit_comment_check_id", "audit_comment", ["check_id"])
    op.create_index("ix_audit_comment_created_at", "audit_comment", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_comment_created_at", table_name="audit_comment")
    op.drop_index("ix_audit_comment_check_id", table_name="audit_comment")
    op.drop_index("ix_audit_comment_author_user_id", table_name="audit_comment")
    op.drop_index("ix_audit_comment_site_page_audit_id", table_name="audit_comment")
    op.drop_table("audit_comment")
    op.drop_index("ix_org_member_user_id", table_name="org_member")
    op.drop_index("ix_org_member_org_id", table_name="org_member")
    op.drop_table("org_member")
    op.drop_index("ix_org_slug", table_name="org")
    op.drop_table("org")
    ORG_ROLE.drop(op.get_bind(), checkfirst=True)
