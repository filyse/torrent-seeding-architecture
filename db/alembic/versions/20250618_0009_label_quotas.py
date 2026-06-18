"""label quotas + torrent meter (phase 5)

Revision ID: 0009
Revises: 0008
Create Date: 2025-06-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "label_quotas",
        sa.Column("label", sa.String(length=128), primary_key=True),
        sa.Column("upload_quota", sa.BigInteger(), nullable=True),
        sa.Column("uploaded_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("exceeded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("paused_ids", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "since", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "torrent_meter",
        sa.Column("torrent_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("last_uploaded", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("torrent_meter")
    op.drop_table("label_quotas")
