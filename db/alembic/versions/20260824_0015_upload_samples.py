"""upload_samples: periodic snapshots of all_time_upload for /network/uploaded charts

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-24

История отдачи — свои снимки в Postgres. Prometheus не используем: горизонт ~15 дней
и он не совпадает с накопителем БД после переноса. Прошлое до этой ревизии не
восстанавливается: график начнёт наполняться с первого cron после деплоя.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upload_samples",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("uploaded", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_samples_sampled_at", "upload_samples", ["sampled_at"])
    op.create_index(
        "ix_upload_samples_scope_at",
        "upload_samples",
        ["scope", "scope_id", "sampled_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_upload_samples_scope_at", table_name="upload_samples")
    op.drop_index("ix_upload_samples_sampled_at", table_name="upload_samples")
    op.drop_table("upload_samples")
