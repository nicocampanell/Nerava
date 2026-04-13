"""Add driver_orders table for tracking in-app browser ordering

Revision ID: 121
Revises: 120
"""

import sqlalchemy as sa
from alembic import op

revision = "121"
down_revision = "120"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "driver_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("merchant_id", sa.String(), nullable=True),
        sa.Column("merchant_name", sa.String(255), nullable=True),
        sa.Column("ordering_url", sa.String(500), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="started"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_driver_orders_driver_id", "driver_orders", ["driver_id"])
    op.create_index("ix_driver_orders_status", "driver_orders", ["status"])
    op.create_index("ix_driver_orders_driver_status", "driver_orders", ["driver_id", "status"])


def downgrade():
    op.drop_index("ix_driver_orders_driver_status", table_name="driver_orders")
    op.drop_index("ix_driver_orders_status", table_name="driver_orders")
    op.drop_index("ix_driver_orders_driver_id", table_name="driver_orders")
    op.drop_table("driver_orders")
