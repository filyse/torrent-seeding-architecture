"""torrents.seeds — сиды роя по скрейпу трекера

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-25

NULL — скрейпа ещё не было. 0 — трекер сказал, что сидов нет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("torrents", sa.Column("seeds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("torrents", "seeds")
