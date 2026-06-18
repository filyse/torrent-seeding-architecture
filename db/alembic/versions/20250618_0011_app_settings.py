"""app settings key-value (global DHT/PEX/LSD policy)

Revision ID: 0011
Revises: 0010
Create Date: 2025-06-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
