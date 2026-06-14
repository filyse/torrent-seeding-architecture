"""add engine_id to torrents

Revision ID: 0002
Revises: 0001
Create Date: 2025-06-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "torrents",
        sa.Column("engine_id", sa.String(length=32), nullable=False, server_default="default"),
    )
    op.create_index("ix_torrents_engine_id", "torrents", ["engine_id"])


def downgrade() -> None:
    op.drop_index("ix_torrents_engine_id", table_name="torrents")
    op.drop_column("torrents", "engine_id")
