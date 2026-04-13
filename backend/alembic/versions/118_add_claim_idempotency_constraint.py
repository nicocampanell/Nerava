"""Add composite index for claim idempotency lookups

The 24-hour duplicate claim check queries (driver_id, merchant_place_id, activated_at).
This index makes that lookback query fast without over-constraining legitimate
repeat visits (a driver CAN revisit the same merchant after 24 hours).

Revision ID: 118
Revises: 117
"""

from alembic import op

revision = "118"
down_revision = "117"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_exclusive_sessions_driver_merchant_activated",
        "exclusive_sessions",
        ["driver_id", "merchant_place_id", "activated_at"],
    )
    op.create_index(
        "ix_exclusive_sessions_driver_merchantid_activated",
        "exclusive_sessions",
        ["driver_id", "merchant_id", "activated_at"],
    )


def downgrade():
    op.drop_index(
        "ix_exclusive_sessions_driver_merchantid_activated",
        table_name="exclusive_sessions",
    )
    op.drop_index(
        "ix_exclusive_sessions_driver_merchant_activated",
        table_name="exclusive_sessions",
    )
