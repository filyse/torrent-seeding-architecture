"""add uploaded_seen to torrents (uploaded_total becomes an accumulator)

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-10

До этой ревизии `torrents.uploaded_total` был ЗЕРКАЛОМ счётчика движка: снимок рантайма
писал в него сырое `total_uploaded` из libtorrent. Счётчик libtorrent живёт в рамках одной
инкарнации торрента, поэтому при переносе на другой движок (торрент добавляется заново)
он стартовал с нуля — и «всего отдано» в UI обнулялось.

Теперь `uploaded_total` — накопитель, а `uploaded_seen` хранит последнее сырое значение
счётчика текущего движка, чтобы считать дельты и ловить сброс (значение уехало назад).

Инициализация: `uploaded_seen = uploaded_total`. Сейчас оба равны текущему показанию
движка, так что «накоплено на прошлых движках» = 0 и первый же тик снимка не задваивает
объём. Задним числом потерянные при прошлых переносах байты не восстанавливаются — этих
данных нигде нет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "torrents",
        sa.Column("uploaded_seen", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE torrents SET uploaded_seen = uploaded_total")


def downgrade() -> None:
    op.drop_column("torrents", "uploaded_seen")
