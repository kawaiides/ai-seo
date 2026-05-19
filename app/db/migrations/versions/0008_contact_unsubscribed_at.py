"""contact.unsubscribed_at — CAN-SPAM / RFC 8058 unsubscribe tracking.

Revision ID: 0008_contact_unsubscribed_at
Revises: 0007_seed_query_usage
Create Date: 2026-05-17

Set when a contact clicks the List-Unsubscribe link. Outbox mailer
filters non-null rows out of every future send.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_contact_unsubscribed_at"
down_revision = "0007_seed_query_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contact",
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_contact_unsubscribed_at", "contact", ["unsubscribed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_contact_unsubscribed_at", table_name="contact")
    op.drop_column("contact", "unsubscribed_at")
