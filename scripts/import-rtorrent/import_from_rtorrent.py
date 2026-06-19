"""Импорт раздач из сессии rtorrent в оркестратор без копирования данных.

Контекст: на хосте-сидбоксе работает rtorrent/ruTorrent, контент разложен по
бакетам /home/rudub/storage/<bucket> (b1, b2, ...). Соответствующий движок этого
бакета монтирует контент read-only в /data/<bucket> (см. docker-compose.b1-content.yml)
и сидирует его «на месте» в режиме seed_mode (без полной перепроверки).

Скрипт запускается НА ХОСТЕ сидбокса. Для каждой раздачи, чья rtorrent-директория
лежит под /downloads/<bucket>:
  - читает <hash>.torrent, определяет name и multi/single;
  - вычисляет save_path в координатах движка:
        multi  -> /data/<bucket>           (libtorrent добавит подпапку <name>)
        single -> /data/<bucket>/<folder>  (файл лежит прямо в этой папке)
  - проверяет наличие контента на хосте;
  - грузит .torrent в оркестратор /api/v1/torrents/upload с seed_mode=true и label=<bucket>.

Идемпотентно: пропускает hash, уже записанные в STATE.
ENV: ORCH, API_KEY, BUCKET (b1), SESSION_DIR, HOST_STORAGE, ENGINE_MOUNT,
     DOWNLOADS_PREFIX, LIMIT (0=все), DRY (1).
"""
import glob
import json
import os
import sys
import urllib.error
import urllib.request

BUCKET = os.environ.get("BUCKET", "b1")
SESS = os.environ.get("SESSION_DIR", "/home/rudub/server/rtorrent/data/rtorrent/.session")
HOST_CONTENT = os.environ.get("HOST_STORAGE", f"/home/rudub/storage/{BUCKET}")
ENGINE_MOUNT = os.environ.get("ENGINE_MOUNT", f"/data/{BUCKET}")
DL_PREFIX = os.environ.get("DOWNLOADS_PREFIX", "downloads")  # компонент пути rtorrent
ORCH = os.environ.get("ORCH", "http://192.168.1.101:8000").rstrip("/")
KEY = os.environ["API_KEY"]
LIMIT = int(os.environ.get("LIMIT", "0"))
DRY = os.environ.get("DRY", "0") == "1"
STATE = os.environ.get("STATE", f"/tmp/{BUCKET}_imported.tsv")


def bdecode(data, i=0):
    c = data[i:i + 1]
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = bdecode(data, i)
            v, i = bdecode(data, i)
            out[k] = v
        return out, i + 1
    j = data.index(b":", i)
    n = int(data[i:j])
    return data[j + 1:j + 1 + n], j + 1 + n


def already_done():
    done = set()
    if os.path.exists(STATE):
        for line in open(STATE, encoding="utf-8"):
            h = line.split("\t", 1)[0].strip()
            if h:
                done.add(h)
    return done


def post_upload(torrent_bytes, filename, fields):
    boundary = "----rtimport7f3a9c"
    parts = []
    for k, v in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        )
    parts.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"torrent_file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: application/x-bittorrent\r\n\r\n"
        ).encode()
        + torrent_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        ORCH + "/api/v1/torrents/upload",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-API-Key": KEY},
    )
    return json.load(urllib.request.urlopen(req, timeout=120))


def main():
    done = already_done()
    files = sorted(glob.glob(os.path.join(SESS, "*.torrent.rtorrent")))
    ok = skip = fail = 0
    out_state = open(STATE, "a", encoding="utf-8")
    for rt_file in files:
        if LIMIT and ok >= LIMIT:
            break
        try:
            meta, _ = bdecode(open(rt_file, "rb").read())
        except Exception:
            continue
        directory = meta.get(b"directory", b"")
        directory = directory.decode("utf-8", "replace") if isinstance(directory, bytes) else ""
        parts = directory.replace("\\", "/").strip("/").split("/")
        if not (DL_PREFIX in parts and parts.index(DL_PREFIX) + 1 < len(parts)
                and parts[parts.index(DL_PREFIX) + 1] == BUCKET):
            continue
        h = os.path.basename(rt_file).split(".")[0]
        if h in done:
            skip += 1
            continue
        tor_path = rt_file[:-len(".rtorrent")]
        if not os.path.exists(tor_path):
            print("NO .torrent", h, file=sys.stderr)
            fail += 1
            continue
        tb = open(tor_path, "rb").read()
        try:
            tdict, _ = bdecode(tb)
            info = tdict[b"info"]
            name = info[b"name"].decode("utf-8", "replace")
            multi = b"files" in info
        except Exception as e:
            print("PARSE FAIL", h, repr(e), file=sys.stderr)
            fail += 1
            continue
        folder = parts[-1]
        save_path = ENGINE_MOUNT if multi else f"{ENGINE_MOUNT}/{folder}"
        host_check = os.path.join(HOST_CONTENT, name if multi else folder)
        if not os.path.exists(host_check):
            print(f"CONTENT MISSING h={h} name={name!r} check={host_check}", file=sys.stderr)
            fail += 1
            continue
        if DRY:
            print(f"DRY import h={h} multi={multi} save_path={save_path} name={name!r}")
            ok += 1
            continue
        try:
            res = post_upload(tb, name + ".torrent", {
                "save_path": save_path, "display_name": name, "label": BUCKET, "seed_mode": "true",
            })
            out_state.write(f"{h}\t{res.get('id')}\t{name}\t{save_path}\n")
            out_state.flush()
            print(f"OK h={h} id={res.get('id')} multi={multi} name={name!r}")
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code} h={h}: {e.read()[:300]!r}", file=sys.stderr)
            fail += 1
        except Exception as e:
            print(f"ERR h={h}: {e!r}", file=sys.stderr)
            fail += 1
    out_state.close()
    print(json.dumps({"ok": ok, "skip": skip, "fail": fail}))


if __name__ == "__main__":
    main()
