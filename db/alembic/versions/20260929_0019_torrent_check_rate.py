"""torrents.check_rate — байт/с проверки по piece_finished_alert.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-29

0 — сейчас куски не хешируются. Не путать со скоростью скачивания.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "torrents",
        sa.Column("check_rate", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("torrents", "check_rate")
