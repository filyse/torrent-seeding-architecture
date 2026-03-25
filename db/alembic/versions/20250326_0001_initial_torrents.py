"""initial torrents table

Revision ID: 0001
Revises:
Create Date: 2025-03-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "torrents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("info_hash", sa.String(length=64), nullable=True),
        sa.Column("magnet_uri", sa.Text(), nullable=True),
        sa.Column("display_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("save_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("info_hash"),
    )


def downgrade() -> None:
    op.drop_table("torrents")
