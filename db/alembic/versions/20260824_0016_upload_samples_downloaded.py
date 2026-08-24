"""upload_samples.downloaded for /network download charts

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-24

Приём пишется в ту же строку сэмпла. NULL у старых строк — «ещё не снимали»,
не ноль: иначе первая дельта стала бы всем all_time_download.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "upload_samples",
        sa.Column("downloaded", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("upload_samples", "downloaded")
