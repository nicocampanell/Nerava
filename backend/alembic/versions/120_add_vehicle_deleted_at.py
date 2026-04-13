"""Add deleted_at column to tesla_connections for soft-delete

Enables soft-delete of vehicles so drivers can remove a connected
vehicle without losing the audit trail. The column is nullable with
a default of NULL (active vehicles have deleted_at=NULL).

Revision ID: 120
Revises: 119
"""

import sqlalchemy as sa
from alembic import op

revision = "120"
down_revision = "119"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tesla_connections",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("tesla_connections", "deleted_at")
