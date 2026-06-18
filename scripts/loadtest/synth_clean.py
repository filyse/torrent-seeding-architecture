"""Удалить синтетические раздачи из runtime движка (db_id >= 1000000) с файлами."""
import json
import os
import ssl
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
TOK = os.environ.get("SEEDING_ENGINE_API_TOKEN", "")
BASE = "https://127.0.0.1:8081/internal/v1/torrents"
HDR = {"X-Engine-Token": TOK}


def call(url, method="GET"):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=HDR, method=method), timeout=30, context=ctx
    )


rows = json.load(call(BASE))
ids = [t["db_id"] for t in rows if int(t.get("db_id", 0)) >= 1000000]
ok = 0
fail = 0
for i in ids:
    try:
        call(f"{BASE}/{i}?delete_files=true", method="DELETE").read()
        ok += 1
    except Exception:  # noqa: BLE001
        fail += 1
print(json.dumps({"removed": ok, "fail": fail, "of": len(ids)}))
