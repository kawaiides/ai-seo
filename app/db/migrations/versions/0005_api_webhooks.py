"""ApiKey + Webhook + WebhookDelivery — Phase E API + embeddability.

Revision ID: 0005_api_webhooks
Revises: 0004_org_comments
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_api_webhooks"
down_revision = "0004_org_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_key",
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
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("prefix", name="uq_api_key_prefix"),
        sa.UniqueConstraint("key_hash", name="uq_api_key_hash"),
    )
    op.create_index("ix_api_key_org_id", "api_key", ["org_id"])
    op.create_index("ix_api_key_prefix", "api_key", ["prefix"])

    op.create_table(
        "webhook",
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
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret", sa.String(length=64), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_webhook_org_id", "webhook", ["org_id"])

    op.create_table(
        "webhook_delivery",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "webhook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_webhook_delivery_webhook_id", "webhook_delivery", ["webhook_id"])
    op.create_index("ix_webhook_delivery_event", "webhook_delivery", ["event"])
    op.create_index("ix_webhook_delivery_attempted_at", "webhook_delivery", ["attempted_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_delivery_attempted_at", table_name="webhook_delivery")
    op.drop_index("ix_webhook_delivery_event", table_name="webhook_delivery")
    op.drop_index("ix_webhook_delivery_webhook_id", table_name="webhook_delivery")
    op.drop_table("webhook_delivery")
    op.drop_index("ix_webhook_org_id", table_name="webhook")
    op.drop_table("webhook")
    op.drop_index("ix_api_key_prefix", table_name="api_key")
    op.drop_index("ix_api_key_org_id", table_name="api_key")
    op.drop_table("api_key")
