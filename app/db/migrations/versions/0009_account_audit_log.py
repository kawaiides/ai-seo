"""account_audit_log — security audit trail.

Revision ID: 0009_account_audit_log
Revises: 0008_contact_unsubscribed_at
Create Date: 2026-05-17

Append-only log of who did what to which account. Distinct from
`funnel_event` (product analytics) and `webhook_event` (idempotency).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_account_audit_log"
down_revision = "0008_contact_unsubscribed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_audit_log",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "subject_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "subject_org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("org.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("request_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_account_audit_log_actor_user_id", "account_audit_log", ["actor_user_id"]
    )
    op.create_index(
        "ix_account_audit_log_subject_user_id", "account_audit_log", ["subject_user_id"]
    )
    op.create_index(
        "ix_account_audit_log_subject_org_id", "account_audit_log", ["subject_org_id"]
    )
    op.create_index("ix_account_audit_log_action", "account_audit_log", ["action"])
    op.create_index("ix_account_audit_log_occurred_at", "account_audit_log", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("account_audit_log")
