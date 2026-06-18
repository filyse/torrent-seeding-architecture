"""Аудит-лог действий (Фаза 5).

Запись ведётся middleware'ом: для всех изменяющих запросов (POST/PUT/PATCH/DELETE) и
для входа фиксируем актора, метод/путь, статус, IP и человекочитаемую сводку. Чтение
журнала — только admin (`routers/audit.py`).
"""

from __future__ import annotations

import os
import random
import re

from fastapi import Request

# Эти пути не пишем в журнал (шумные/служебные хартбиты движков).
_SKIP_PREFIXES = ("/api/v1/engines/register",)

# Методы, которые считаем действиями.
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# (метод-или-*, регулярка по пути без префикса /api/v1) -> шаблон сводки.
# {1}, {2}… — группы из регулярки.
_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("POST", re.compile(r"^/auth/login$"), "Вход в систему"),
    ("POST", re.compile(r"^/auth/key-login$"), "Вход по API-ключу"),
    ("POST", re.compile(r"^/auth/logout$"), "Выход из системы"),
    ("POST", re.compile(r"^/auth/users$"), "Создан пользователь"),
    ("PATCH", re.compile(r"^/auth/users/(\d+)$"), "Изменён пользователь #{1}"),
    ("DELETE", re.compile(r"^/auth/users/(\d+)$"), "Удалён пользователь #{1}"),
    ("POST", re.compile(r"^/auth/keys$"), "Создан API-ключ"),
    ("PATCH", re.compile(r"^/auth/keys/(\d+)$"), "Изменён API-ключ #{1}"),
    ("DELETE", re.compile(r"^/auth/keys/(\d+)$"), "Удалён API-ключ #{1}"),
    ("POST", re.compile(r"^/backups$"), "Создан бэкап БД"),
    ("POST", re.compile(r"^/backups/restore$"), "Восстановление БД из бэкапа"),
    ("POST", re.compile(r"^/session/limits$"), "Изменены глобальные лимиты"),
    ("POST", re.compile(r"^/torrents$"), "Добавлен торрент"),
    ("POST", re.compile(r"^/torrents/upload(-batch)?$"), "Загружен torrent-файл"),
    ("POST", re.compile(r"^/torrents/url$"), "Добавлен торрент по ссылке"),
    ("POST", re.compile(r"^/torrents/bulk/pause$"), "Массовая пауза раздач"),
    ("POST", re.compile(r"^/torrents/bulk/resume$"), "Массовый запуск раздач"),
    ("POST", re.compile(r"^/torrents/bulk/label$"), "Массовая смена метки"),
    ("POST", re.compile(r"^/torrents/bulk/delete$"), "Массовое удаление раздач"),
    ("POST", re.compile(r"^/torrents/(\d+)/migrate$"), "Перенос торрента #{1}"),
    ("POST", re.compile(r"^/torrents/(\d+)/limits$"), "Изменены лимиты торрента #{1}"),
    ("POST", re.compile(r"^/torrents/(\d+)/pause$"), "Пауза торрента #{1}"),
    ("POST", re.compile(r"^/torrents/(\d+)/resume$"), "Запуск торрента #{1}"),
    ("POST", re.compile(r"^/torrents/(\d+)/recheck$"), "Перепроверка торрента #{1}"),
    ("POST", re.compile(r"^/torrents/(\d+)/reannounce$"), "Реанонс торрента #{1}"),
    ("POST", re.compile(r"^/torrents/(\d+)/files/priorities$"), "Приоритеты файлов торрента #{1}"),
    ("POST", re.compile(r"^/torrents/(\d+)/trackers$"), "Добавлен трекер торрента #{1}"),
    ("DELETE", re.compile(r"^/torrents/(\d+)/trackers$"), "Удалён трекер торрента #{1}"),
    ("PATCH", re.compile(r"^/torrents/(\d+)$"), "Изменён торрент #{1}"),
    ("DELETE", re.compile(r"^/torrents/(\d+)$"), "Удалён торрент #{1}"),
    ("POST", re.compile(r"^/(noop|engine-health-check|sync-runtime|restore-all)$"),
     "Сервисная задача: {1}"),
    ("POST", re.compile(r"^/(bulk-register|restore-engine)/([\w-]+)$"),
     "Сервис движка {2}: {1}"),
]


def summarize(method: str, path: str) -> str:
    p = path[len("/api/v1"):] if path.startswith("/api/v1") else path
    for m, rx, tmpl in _RULES:
        if m != method:
            continue
        match = rx.match(p)
        if match:
            out = tmpl
            for i, g in enumerate(match.groups(), start=1):
                out = out.replace(f"{{{i}}}", g or "")
            return out
    return f"{method} {p}"


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def should_record(method: str, path: str) -> bool:
    if method not in _MUTATING:
        return False
    if not path.startswith("/api/v1"):
        return False
    return not path.startswith(_SKIP_PREFIXES)


def _retention_days() -> int:
    try:
        return max(1, int(os.getenv("SEEDING_AUDIT_RETENTION_DAYS", "90")))
    except ValueError:
        return 90


async def record(request: Request, status: int) -> None:
    """Записать событие в аудит. Безопасно: ошибки глушим, чтобы не ломать ответ."""
    method = request.method
    path = request.url.path
    if not should_record(method, path):
        return
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return

    actor, role = "anonymous", ""
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        actor, role = principal.name, principal.role
    else:
        a = getattr(request.state, "audit_actor", None)
        if a:
            actor = str(a)

    summary = summarize(method, path)
    ip = _client_ip(request)
    try:
        from seeding_db.repository import AuditRepository

        async with factory() as session:
            repo = AuditRepository(session)
            await repo.add(
                actor=actor, role=role, method=method, path=path,
                status=status, ip=ip, summary=summary,
            )
            # Изредка подчищаем старые записи, чтобы таблица не росла бесконечно.
            if random.random() < 0.02:
                await repo.trim_older_than(_retention_days())
            await session.commit()
    except Exception:
        pass
