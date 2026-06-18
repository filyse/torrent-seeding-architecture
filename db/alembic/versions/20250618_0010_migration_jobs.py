"""migration jobs (resumable migration, phase 4)

Revision ID: 0010
Revises: 0009
Create Date: 2025-06-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "migration_jobs",
        sa.Column("torrent_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("source_engine_id", sa.String(length=64), nullable=False),
        sa.Column("target_engine_id", sa.String(length=64), nullable=False),
        sa.Column("source_save_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_save_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("src_content_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("display_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("transport", sa.String(length=16), nullable=False, server_default="media"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(length=32), nullable=False, server_default="preparing"),
        sa.Column("copied", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("migration_jobs")
