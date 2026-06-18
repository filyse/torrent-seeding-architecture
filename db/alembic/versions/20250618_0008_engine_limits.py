"""persistent engine session limits (phase 5)

Revision ID: 0008
Revises: 0007
Create Date: 2025-06-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("engines", sa.Column("download_limit", sa.Integer(), nullable=True))
    op.add_column("engines", sa.Column("upload_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("engines", "upload_limit")
    op.drop_column("engines", "download_limit")
