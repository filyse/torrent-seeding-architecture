"""Структурные (JSON) логи (Фаза 6).

По умолчанию — обычный текстовый лог. Если `SEEDING_LOG_JSON=1` — корневой логгер
переключается на построчный JSON (удобно для сбора/парсинга). Уровень — `LOG_LEVEL`."""

from __future__ import annotations

import json
import logging
import os
import sys
import time

_EXTRA_FIELDS = ("engine_id", "torrent_id", "job", "event")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        for key in _EXTRA_FIELDS:
            val = getattr(record, key, None)
            if val is not None:
                data[key] = val
        return json.dumps(data, ensure_ascii=False)


def setup_logging(service: str) -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.setLevel(level)
    if os.getenv("SEEDING_LOG_JSON", "").lower() in ("1", "true", "yes"):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter(service))
        root.handlers[:] = [handler]
        # uvicorn ставит свои хендлеры на отдельные логгеры — перенаправим их в JSON.
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            lg = logging.getLogger(name)
            lg.handlers[:] = [handler]
            lg.propagate = False
    elif not root.handlers:
        logging.basicConfig(level=level)
