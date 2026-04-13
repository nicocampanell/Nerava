"""Add offer_url column to campaigns table

Allows campaigns to store an external URL for partner/sponsor offers
(e.g., EVject discount link). The driver app reads this field to open
the correct URL when a driver taps a partner offer card.

Revision ID: 119
Revises: 118
"""

import sqlalchemy as sa
from alembic import op

revision = "119"
down_revision = "118"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campaigns",
        sa.Column("offer_url", sa.String(500), nullable=True),
    )


def downgrade():
    op.drop_column("campaigns", "offer_url")
