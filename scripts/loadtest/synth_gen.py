"""Генератор синтетических раздач ВНУТРИ движка (для нагрузочного теста).

Создаёт COUNT крошечных trackerless-торрентов: контент в /data/phase3, .torrent строится
libtorrent'ом по реальному контенту, регистрируется через локальный internal API движка
(https + token). db_id берём с большим оффсетом, чтобы не пересекаться с реальными (<1000).

Usage (внутри контейнера движка):
    python3 synth_gen.py COUNT OFFSET
"""
import base64
import json
import os
import ssl
import sys
import time
import urllib.request

import libtorrent as lt

COUNT = int(sys.argv[1])
OFFSET = int(sys.argv[2])
SAVE_DIR = "/data/phase3"
TOKEN = os.environ.get("SEEDING_ENGINE_API_TOKEN", "")
FILE_SIZE = 64 * 1024
PIECE_SIZE = 16 * 1024

os.makedirs(SAVE_DIR, exist_ok=True)
os.chdir(SAVE_DIR)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def build_torrent(fname: str) -> bytes:
    fs = lt.file_storage()
    lt.add_files(fs, fname)
    ct = lt.create_torrent(fs, PIECE_SIZE)
    ct.set_priv(False)
    lt.set_piece_hashes(ct, SAVE_DIR)
    return lt.bencode(ct.generate())


def register(db_id: int, b64: str) -> None:
    body = json.dumps({"db_id": db_id, "torrent_b64": b64, "save_path": SAVE_DIR}).encode()
    req = urllib.request.Request(
        "https://127.0.0.1:8081/internal/v1/torrents",
        data=body,
        headers={"Content-Type": "application/json", "X-Engine-Token": TOKEN},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30, context=ctx).read()


ok = 0
fail = 0
t0 = time.time()
for i in range(COUNT):
    db_id = OFFSET + i
    fname = f"s{db_id}.bin"
    try:
        with open(os.path.join(SAVE_DIR, fname), "wb") as f:
            f.write(os.urandom(FILE_SIZE))
        b64 = base64.b64encode(build_torrent(fname)).decode()
        register(db_id, b64)
        ok += 1
    except Exception as e:  # noqa: BLE001
        fail += 1
        if fail <= 3:
            print("FAIL", db_id, repr(e), file=sys.stderr)

print(json.dumps({"ok": ok, "fail": fail, "secs": round(time.time() - t0, 1)}))
