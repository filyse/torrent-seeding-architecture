"""Суточный бэкап состояния движков: `python3 -m seeding_api.backup_engines`.

Дамп Postgres делает `scripts/db-backup.sh`, а этот модуль добирает то, чего в БД нет:
`.fastresume`, `.torrents` и `session.state` каждого движка. Они лежат в docker-томах на
хостах движков и больше нигде не дублируются — без них восстановление означает полный
рехэш всего контента.

Почему изнутри контейнера api, а не по SSH с хоста: оркестратор общается с движками
единственным каналом — внутренним HTTP API (TLS + токен), и реестр движков (env + БД) он
уже знает. Так бэкап автоматически покрывает и вновь зарегистрированные движки, и не
требует заводить отдельное SSH-доверие от CT400 к хостам движков.

Env: SEEDING_BACKUP_DIR (/backups), SEEDING_BACKUP_RETENTION_DAYS (14).
Код возврата: 0 — все движки сняты; 1 — часть; 2 — ни одного (в т.ч. если движков нет).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from seeding_db.config import get_database_url
from seeding_db.session import create_engine, create_session_factory

from seeding_api.engine_pool import EnginePool

log = logging.getLogger("backup_engines")

_CHUNK = 1024 * 1024


def backup_dir() -> Path:
    return Path(os.getenv("SEEDING_BACKUP_DIR", "/backups")) / "engines"


def retention_days() -> int:
    try:
        return max(1, int(os.getenv("SEEDING_BACKUP_RETENTION_DAYS", "14")))
    except ValueError:
        return 14


async def dump_engine(pool: EnginePool, engine_id: str, dest_dir: Path, stamp: str) -> int:
    """Скачать архив одного движка. Возвращает размер в байтах (0 — не удалось).

    Пишем в `.partial` и переименовываем в конце: оборванная закачка не должна выглядеть
    как валидный бэкап (та же схема, что в db-backup.sh).
    """
    out = dest_dir / f"{engine_id}-{stamp}.tar.gz"
    tmp = out.with_suffix(out.suffix + ".partial")
    client = pool.client_for(engine_id)
    written = 0
    try:
        async with client.stream_meta_archive() as (resp, files):
            with tmp.open("wb") as fh:
                async for chunk in resp.aiter_bytes(_CHUNK):
                    fh.write(chunk)
                    written += len(chunk)
        tmp.rename(out)
        log.info("%s: %s байт, файлов в архиве ~%s -> %s", engine_id, written, files, out.name)
        return written
    except Exception as exc:  # noqa: BLE001 — один недоступный движок не валит весь проход
        log.warning("%s: не удалось снять метаданные: %s", engine_id, exc)
        tmp.unlink(missing_ok=True)
        return 0


def prune(dest_dir: Path, days: int) -> int:
    """Удалить архивы старше N дней. Возвращает число удалённых."""
    cutoff = time.time() - days * 86400
    removed = 0
    for path in dest_dir.glob("*.tar.gz"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            log.warning("не удалось удалить %s: %s", path.name, exc)
    return removed


async def run() -> int:
    dest = backup_dir()
    dest.mkdir(parents=True, exist_ok=True)

    engine = create_engine(get_database_url())
    session_factory = create_session_factory(engine)
    pool = EnginePool(session_factory=session_factory)
    try:
        await pool.refresh()
        engine_ids = sorted(s.id for s in pool.specs)
        if not engine_ids:
            log.error("реестр движков пуст — нечего бэкапить")
            return 2

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        # Последовательно, а не параллельно: движки на HDD, и одновременное чтение
        # метаданных с нескольких дало бы лишние seek'и в ущерб раздаче.
        sizes = [await dump_engine(pool, eid, dest, stamp) for eid in engine_ids]
    finally:
        await pool.aclose()
        await engine.dispose()

    ok = sum(1 for s in sizes if s > 0)
    total = sum(sizes)
    removed = prune(dest, retention_days())
    log.info(
        "готово: движков %s/%s, суммарно %.1f МБ, удалено старых архивов %s (хранится %s дн.)",
        ok, len(engine_ids), total / 1024 / 1024, removed, retention_days(),
    )
    if ok == 0:
        return 2
    return 0 if ok == len(engine_ids) else 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx на INFO печатает строку на каждый запрос — в суточном cron-логе это шум,
    # который прячет собственно результат по каждому движку.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
