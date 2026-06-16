"""dynamic engine registry table

Revision ID: 0004
Revises: 0003
Create Date: 2025-06-17

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "engines",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("storage_prefix", sa.Text(), nullable=False, server_default=""),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("listen_port", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("engines")
