"""Диск раздачи — отдельный том, а не пустой каталог на системном диске.

После обрыва питания строка fstab с `nofail` не задерживает загрузку. Docker
успевает примонтировать путь до диска, libtorrent видит пустой каталог и
начинает качать библиотеку заново, затирая fastresume.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

_ON = frozenset({"1", "true", "yes", "on"})


def require_mount_enabled() -> bool:
    """`SEEDING_STORAGE_REQUIRE_MOUNT=1` — не стартовать, пока каталог раздачи не отдельный том."""
    raw = os.getenv("SEEDING_STORAGE_REQUIRE_MOUNT", "").strip().lower()
    return raw in _ON


class StorageNotMounted(RuntimeError):
    """Каталог раздачи не на отдельном диске. Раздачи открывать нельзя."""


def mount_problem(
    root: Path,
    subdir: str,
    *,
    device: Callable[[Path], int] | None = None,
) -> str | None:
    """Текст ошибки, если подкаталог раздачи не отдельный диск. None — можно стартовать."""
    sub = subdir.strip().strip("/\\")
    if not sub:
        return "SEEDING_STORAGE_REQUIRE_MOUNT=1, но ENGINE_STORAGE_SUBDIR пуст"
    content = root / sub
    dev = device if device is not None else (lambda p: p.stat().st_dev)
    if not content.is_dir():
        return (
            f"диск раздачи {content} не смонтирован (каталога нет). "
            "Движок не стартует, чтобы не скачать библиотеку на системный диск."
        )
    try:
        same = dev(content) == dev(root)
    except OSError as exc:
        return f"не удалось прочитать диск {content}: {exc}"
    if same:
        return (
            f"диск раздачи {content} не смонтирован: тот же filesystem, что и {root}. "
            "Движок не стартует, чтобы не скачать библиотеку на системный диск."
        )
    return None


def assert_content_mounted(root: Path | None = None, subdir: str | None = None) -> None:
    """Ничего не делает, пока настройка выключена. Иначе System-уровень: процесс не должен жить."""
    if not require_mount_enabled():
        return
    if root is None:
        root = Path(os.getenv("SEEDING_DATA_ROOT", "/data"))
    if subdir is None:
        subdir = os.getenv("ENGINE_STORAGE_SUBDIR", "")
    problem = mount_problem(root, subdir)
    if problem:
        raise StorageNotMounted(problem)


def files_cover(save_path: Path, files: list[tuple[str, int]]) -> bool:
    """Все файлы раздачи уже лежат целиком. Сравнивается размер, содержимое не читается."""
    if not files:
        return False
    root = save_path.resolve()
    for rel, size in files:
        if size < 0:
            return False
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            return False
        path = (root / rel_path).resolve()
        if path != root and root not in path.parents:
            return False
        try:
            if not path.is_file() or path.stat().st_size < int(size):
                return False
        except OSError:
            return False
    return True
