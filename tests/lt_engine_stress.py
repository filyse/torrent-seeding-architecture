"""Стресс настоящего LibtorrentTorrentRuntime: add/recheck/remove/pause/poll.

Повторяет то, что CT400 делает с движком пачками: удалить раздачу и сразу
добавить новые, пока идут проверки из очереди и опрос статусов.
Запуск отдельным процессом (ошибка — SIGSEGV):

  python tests/lt_engine_stress.py 30
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
import time

ROOT = tempfile.mkdtemp()
os.environ.setdefault("SEEDING_DATA_ROOT", ROOT)
os.environ.setdefault("LT_LISTEN_INTERFACES", "127.0.0.1:0")
os.environ.setdefault("SEEDING_HASH_PER_ENGINE", "2")
os.environ.pop("SEEDING_LT_STATE_FILE", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

import libtorrent as lt  # noqa: E402
from seeding_engine.torrent_runtime import LibtorrentTorrentRuntime  # noqa: E402


def _torrents(n: int) -> list[bytes]:
    out = []
    for i in range(n):
        p = os.path.join(ROOT, f"f{i}")
        with open(p, "wb") as f:
            f.write(os.urandom(512 * 1024))
        fs = lt.file_storage()
        lt.add_files(fs, p)
        t = lt.create_torrent(fs, 16384)
        lt.set_piece_hashes(t, ROOT)
        out.append(lt.bencode(t.generate()))
    return out


async def main(seconds: float) -> None:
    data = _torrents(30)
    rt = LibtorrentTorrentRuntime()
    await rt.start()
    stop = time.monotonic() + seconds
    live: set[int] = set()
    stats = {"add": 0, "rm": 0, "recheck": 0, "poll": 0}

    async def churn() -> None:
        while time.monotonic() < stop:
            i = random.randrange(len(data))
            try:
                if i in live:
                    live.discard(i)
                    await rt.remove(i)
                    stats["rm"] += 1
                else:
                    live.add(i)
                    await rt.add_torrent(i, None, ROOT, torrent_data=data[i])
                    await rt.recheck(i)
                    stats["add"] += 1
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0)

    async def rechecker() -> None:
        while time.monotonic() < stop:
            for i in list(live):
                try:
                    await rt.recheck(i)
                    stats["recheck"] += 1
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(0.001)

    async def poller() -> None:
        while time.monotonic() < stop:
            try:
                await rt.list_all()
                for i in list(live):
                    await rt.pause(i)
                    await rt.resume(i)
                stats["poll"] += 1
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.01)

    await asyncio.gather(churn(), churn(), churn(), rechecker(), poller())
    await rt.stop()
    print("survived", stats)


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 20.0))
