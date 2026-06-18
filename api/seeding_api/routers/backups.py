"""Управление резервными копиями БД из UI (Фаза 5 / эксплуатация).

Список дампов, создание «сейчас» и откат на выбранный дамп. Дампы лежат в каталоге,
смонтированном в контейнер api (`SEEDING_BACKUP_DIR`, по умолчанию /backups), и
совпадают с тем, что пишет cron-скрипт `scripts/db-backup.sh` (pg_dump -Fc).

Все операции — только для роли admin. pg_dump/pg_restore берутся из postgresql-client
и ходят к БД по TCP (host из DATABASE_URL).
"""

import asyncio
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from seeding_api import maintenance
from seeding_api.auth import Principal, require_admin

router = APIRouter(tags=["backups"])

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.dump$")


def _backup_dir() -> str:
    return os.getenv("SEEDING_BACKUP_DIR", "/backups")


def _db_conn() -> dict:
    url = os.getenv("DATABASE_URL", "")
    parts = urlsplit(url)
    return {
        "host": parts.hostname or "db",
        "port": str(parts.port or 5432),
        "user": unquote(parts.username) if parts.username else "seeding",
        "password": unquote(parts.password) if parts.password else "",
        "db": (parts.path or "/seeding").lstrip("/") or "seeding",
    }


def _safe_path(filename: str) -> str:
    name = os.path.basename(filename)
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="недопустимое имя файла")
    path = os.path.join(_backup_dir(), name)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(_backup_dir()):
        raise HTTPException(status_code=400, detail="путь вне каталога бэкапов")
    return path


async def _run(cmd: list[str], password: str) -> tuple[int, str]:
    env = {**os.environ, "PGPASSWORD": password}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


class RestoreIn(BaseModel):
    filename: str


@router.get("/backups")
async def list_backups(_: Principal = Depends(require_admin)):
    d = _backup_dir()
    items = []
    try:
        for name in os.listdir(d):
            if not name.endswith(".dump"):
                continue
            full = os.path.join(d, name)
            try:
                stt = os.stat(full)
            except OSError:
                continue
            items.append(
                {
                    "filename": name,
                    "size": stt.st_size,
                    "created_at": datetime.fromtimestamp(stt.st_mtime, timezone.utc).isoformat(),
                }
            )
    except FileNotFoundError:
        return {"dir": d, "available": False, "items": []}
    items.sort(key=lambda x: x["filename"], reverse=True)
    return {"dir": d, "available": True, "items": items}


@router.post("/backups", status_code=201)
async def create_backup(_: Principal = Depends(require_admin)):
    d = _backup_dir()
    if not os.path.isdir(d):
        raise HTTPException(status_code=500, detail=f"каталог бэкапов недоступен: {d}")
    conn = _db_conn()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{conn['db']}-{stamp}.dump"
    path = os.path.join(d, name)
    code, log = await _run(
        ["pg_dump", "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
         "-d", conn["db"], "-Fc", "-f", path],
        conn["password"],
    )
    if code != 0:
        raise HTTPException(status_code=500, detail=f"pg_dump завершился с ошибкой: {log[-500:]}")
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return {"filename": name, "size": size}


@router.get("/backups/{filename}/download")
async def download_backup(filename: str, _: Principal = Depends(require_admin)):
    path = _safe_path(filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="файл бэкапа не найден")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=os.path.basename(path),
    )


@router.delete("/backups/{filename}")
async def delete_backup(filename: str, _: Principal = Depends(require_admin)):
    path = _safe_path(filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="файл бэкапа не найден")
    try:
        os.remove(path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"не удалось удалить: {exc}") from exc
    return {"deleted": os.path.basename(path)}


@router.post("/backups/restore")
async def restore_backup(body: RestoreIn, _: Principal = Depends(require_admin)):
    path = _safe_path(body.filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="файл бэкапа не найден")
    conn = _db_conn()
    # Мягкий режим обслуживания: на время restore API отвечает 503 на рабочие маршруты,
    # чтобы клиенты не ходили в БД, пока таблицы пересоздаются.
    maintenance.begin("Идёт восстановление БД, подождите…", ttl_seconds=600.0)
    try:
        code, log = await _run(
            ["pg_restore", "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
             "-d", conn["db"], "--clean", "--if-exists", "--no-owner", path],
            conn["password"],
        )
    finally:
        maintenance.end()
    # pg_restore может вернуть ненулевой код из-за безобидных предупреждений (например,
    # DROP несуществующего объекта). Считаем успехом, если нет строк с "error".
    has_error = bool(re.search(r"\berror\b", log, re.IGNORECASE))
    if code != 0 and has_error:
        raise HTTPException(status_code=500, detail=f"pg_restore: {log[-800:]}")
    return {"ok": True, "filename": body.filename, "warnings": log[-800:] if log.strip() else ""}
