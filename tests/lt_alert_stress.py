"""Стресс libtorrent: параллельные add/remove/recheck/poll + разбор алертов.

Запускается отдельным процессом из tests/test_alert_lifetime.py, потому что
ошибка, которую он ловит, — SIGSEGV, а не исключение.

  python tests/lt_alert_stress.py fixed   # путь движка: снимки под ALERT_LOCK
  python tests/lt_alert_stress.py buggy   # старый путь: держим алерты между pop
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

import libtorrent as lt  # noqa: E402
from seeding_engine.fastresume_io import (  # noqa: E402
    pop_alert_events,
    save_resume_data_blocking,
)


def _make_infos(root: str, n: int) -> list:
    infos = []
    for i in range(n):
        p = os.path.join(root, f"f{i}")
        with open(p, "wb") as f:
            f.write(os.urandom(256 * 1024))
        fs = lt.file_storage()
        lt.add_files(fs, p)
        t = lt.create_torrent(fs, 16384)
        t.set_priv(True)
        lt.set_piece_hashes(t, root)
        infos.append(lt.torrent_info(lt.bencode(t.generate())))
    return infos


def main(mode: str, seconds: float) -> None:
    root = tempfile.mkdtemp()
    os.environ["SEEDING_FASTRESUME_DIR"] = os.path.join(root, ".fastresume")
    infos = _make_infos(root, 40)
    ses = lt.session({
        "alert_mask": lt.alert.category_t.all_categories,
        "listen_interfaces": "127.0.0.1:0",
        "enable_dht": False,
        "enable_lsd": False,
    })
    handles: dict[int, object] = {}
    hlock = threading.Lock()
    stop = time.monotonic() + seconds
    held: list = []

    def churn() -> None:
        while time.monotonic() < stop:
            i = random.randrange(len(infos))
            with hlock:
                h = handles.pop(i, None)
            if h is not None:
                ses.remove_torrent(h)
            else:
                h = ses.add_torrent({"ti": infos[i], "save_path": root})
                h.force_recheck()
                with hlock:
                    handles[i] = h

    def poll() -> None:
        while time.monotonic() < stop:
            with hlock:
                hs = list(handles.values())
            for h in hs:
                try:
                    h.status()
                except Exception:  # noqa: BLE001
                    pass

    def drain() -> None:
        nonlocal held
        while time.monotonic() < stop:
            if mode == "buggy":
                held.extend(list(ses.pop_alerts()))
                held = held[-5000:]
                for a in held:  # то, что делал _account_piece_alerts по «side»
                    h = getattr(a, "handle", None)
                    if h is not None:
                        h.is_valid()
            else:
                events = pop_alert_events(lt, ses)
                held.extend(events)
                held = held[-5000:]
                for ev in held:  # снимки безопасно трогать после следующих pop
                    try:
                        if ev.handle.is_valid():
                            ev.handle.status()
                    except Exception:  # noqa: BLE001
                        pass
            time.sleep(0.005)

    def saver() -> None:
        while time.monotonic() < stop:
            with hlock:
                snap = dict(handles)
            if mode != "buggy":
                save_resume_data_blocking(lt, ses, snap, timeout=1.0)
            time.sleep(0.05)

    ts = [threading.Thread(target=f) for f in (churn, churn, poll, drain, saver)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print("survived", mode)


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 20.0)
