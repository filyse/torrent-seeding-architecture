"""snapshot live runtime fields on torrents (up/down/peers/progress/uploaded/size)

Revision ID: 0012
Revises: 0011
Create Date: 2025-06-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("torrents", sa.Column("up_rate", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("torrents", sa.Column("down_rate", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("torrents", sa.Column("peers", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("torrents", sa.Column("progress", sa.Float(), nullable=False, server_default="0"))
    op.add_column(
        "torrents", sa.Column("uploaded_total", sa.BigInteger(), nullable=False, server_default="0")
    )
    op.add_column("torrents", sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("torrents", sa.Column("runtime_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_torrents_up_rate", "torrents", ["up_rate"])


def downgrade() -> None:
    op.drop_index("ix_torrents_up_rate", table_name="torrents")
    op.drop_column("torrents", "runtime_at")
    op.drop_column("torrents", "size")
    op.drop_column("torrents", "uploaded_total")
    op.drop_column("torrents", "progress")
    op.drop_column("torrents", "peers")
    op.drop_column("torrents", "down_rate")
    op.drop_column("torrents", "up_rate")
