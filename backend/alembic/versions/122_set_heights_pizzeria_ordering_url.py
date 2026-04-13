"""Set Heights Pizzeria ordering_url to Toast online ordering page

The ordering_url column already exists on the merchants table (WYC).
This migration populates it for The Heights Pizzeria so the driver
app can open the Toast in-app browser for ordering.

Revision ID: 122
Revises: 121
"""

from alembic import op

revision = "122"
down_revision = "121"
branch_labels = None
depends_on = None

TOAST_URL = "https://www.toasttab.com/local/order/the-heights"


def upgrade():
    op.execute(
        "UPDATE merchants"
        "  SET ordering_url = '" + TOAST_URL + "'"
        "  WHERE LOWER(name) LIKE '%heights pizzeria%'"
    )


def downgrade():
    op.execute(
        "UPDATE merchants"
        "  SET ordering_url = NULL"
        "  WHERE LOWER(name) LIKE '%heights pizzeria%'"
        "  AND ordering_url = '" + TOAST_URL + "'"
    )
