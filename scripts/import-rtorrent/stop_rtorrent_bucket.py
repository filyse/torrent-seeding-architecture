"""Аккуратно остановить (d.stop+d.close, НЕ удалять) импортированные раздачи в rtorrent.

Запуск ВНУТРИ контейнера rtorrent (есть python3 + scgi.socket). Защита: перед
остановкой повторно проверяем d.directory — трогаем только раздачи под
/downloads/<bucket>. d.stop обратим: d.start вернёт сидирование.

argv[1]: dry | stop
ENV: BUCKET (b1), HASHFILE (/tmp/<bucket>_imported.tsv), SCGI_SOCKET.
"""
import os
import socket
import sys
import xmlrpc.client

BUCKET = os.environ.get("BUCKET", "b1")
SOCK = os.environ.get("SCGI_SOCKET", "/var/run/rtorrent/scgi.socket")
HASHFILE = os.environ.get("HASHFILE", f"/tmp/{BUCKET}_imported.tsv")
MODE = sys.argv[1] if len(sys.argv) > 1 else "dry"
EXPECT_PREFIX = f"/downloads/{BUCKET}/"


def scgi_call(method, params):
    xml = xmlrpc.client.dumps(tuple(params), method).encode("utf-8")
    headers = b"".join(
        k + b"\0" + v + b"\0"
        for k, v in [
            (b"CONTENT_LENGTH", str(len(xml)).encode()),
            (b"SCGI", b"1"),
            (b"REQUEST_METHOD", b"POST"),
            (b"REQUEST_URI", b"/RPC2"),
        ]
    )
    req = b"%d:%s," % (len(headers), headers) + xml
    s = socket.socket(socket.AF_UNIX)
    s.connect(SOCK)
    s.sendall(req)
    buf = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    body = buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else buf
    p, u = xmlrpc.client.getparser()
    p.feed(body)
    p.close()
    r = u.close()
    return r[0] if isinstance(r, tuple) and len(r) == 1 else r


def main():
    hashes = []
    for line in open(HASHFILE, encoding="utf-8"):
        h = line.split("\t", 1)[0].strip().upper()
        if len(h) == 40:
            hashes.append(h)
    print(f"loaded {len(hashes)} hashes; bucket={BUCKET}; mode={MODE}")
    stopped = would = already = not_b = missing = err = 0
    for i, h in enumerate(hashes, 1):
        try:
            directory = str(scgi_call("d.directory", [h]))
        except Exception as e:
            print("MISSING/ERR", h, repr(e)[:80], file=sys.stderr)
            missing += 1
            continue
        if not directory.startswith(EXPECT_PREFIX):
            print(f"SKIP not-{BUCKET} h={h} dir={directory!r}", file=sys.stderr)
            not_b += 1
            continue
        try:
            state = scgi_call("d.state", [h])
        except Exception:
            state = None
        if state == 0:
            already += 1
            continue
        if MODE != "stop":
            would += 1
            if i <= 3:
                print(f"DRY would stop h={h} dir={directory}")
            continue
        try:
            scgi_call("d.stop", [h])
            scgi_call("d.close", [h])
            stopped += 1
            if stopped % 200 == 0:
                print(f"...stopped {stopped}")
        except Exception as e:
            print("STOP ERR", h, repr(e)[:80], file=sys.stderr)
            err += 1
    print({
        "total": len(hashes), "stopped": stopped, "would_stop": would,
        "already_stopped": already, "not_bucket_skipped": not_b, "missing": missing, "err": err,
    })


if __name__ == "__main__":
    main()
