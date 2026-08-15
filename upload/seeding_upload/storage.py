"""Staging в /data/<engine>/.upload-tmp и финальный move в целевой каталог."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Как у контента движков / FTP (rudub, rudub2 → uid/gid 1000).
DEFAULT_CONTENT_UID = 1000
DEFAULT_CONTENT_GID = 1000
DEFAULT_CONTENT_MODE = 0o644
DEFAULT_DIR_MODE = 0o755


META_NAME = "meta.json"
CHUNKS_DIR = "chunks"


@dataclass
class UploadSession:
    id: str
    engine_id: str
    dest_dir: str
    filename: str
    size: int
    chunk_size: int
    jti: str
    overwrite: bool = False
    cancel_at: float | None = None
    created_at: float = field(default_factory=time.time)

    @property
    def chunk_count(self) -> int:
        if self.size == 0:
            return 0
        return (self.size + self.chunk_size - 1) // self.chunk_size


class StorageError(ValueError):
    pass


class UploadStorage:
    def __init__(self, roots: dict[str, Path], chunk_size: int, gc_minutes: float):
        self.roots = {k: Path(v) for k, v in roots.items()}
        self.chunk_size = chunk_size
        self.gc_seconds = max(60.0, gc_minutes * 60.0)

    def root_for(self, engine_id: str) -> Path:
        root = self.roots.get(engine_id)
        if root is None:
            raise StorageError(f"engine {engine_id} not mounted on this upload node")
        return root

    def staging_base(self, engine_id: str) -> Path:
        return self.root_for(engine_id) / ".upload-tmp"

    def session_dir(self, engine_id: str, upload_id: str) -> Path:
        return self.staging_base(engine_id) / upload_id

    def _safe_dest_dir(self, engine_id: str, dest_dir: str) -> Path:
        root = self.root_for(engine_id).resolve()
        dest = Path(dest_dir)
        if not dest.is_absolute():
            raise StorageError("dest_dir must be absolute")
        resolved = dest.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise StorageError("dest_dir outside engine root") from exc
        # Нельзя писать в чужой staging как «цель».
        if resolved == (root / ".upload-tmp").resolve() or str(resolved).startswith(
            str((root / ".upload-tmp").resolve()) + os.sep
        ):
            raise StorageError("dest_dir must not be inside .upload-tmp")
        return resolved

    def create(
        self,
        *,
        upload_id: str,
        engine_id: str,
        dest_dir: str,
        filename: str,
        size: int,
        jti: str,
        overwrite: bool,
    ) -> UploadSession:
        dest = self._safe_dest_dir(engine_id, dest_dir)
        final = dest / filename
        if final.exists() and not overwrite:
            raise StorageError("file exists; confirm overwrite")
        sess = UploadSession(
            id=upload_id,
            engine_id=engine_id,
            dest_dir=str(dest),
            filename=filename,
            size=size,
            chunk_size=self.chunk_size,
            jti=jti,
            overwrite=overwrite,
        )
        d = self.session_dir(engine_id, upload_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / CHUNKS_DIR).mkdir(exist_ok=True)
        self._write_meta(sess)
        return sess

    def _write_meta(self, sess: UploadSession) -> None:
        path = self.session_dir(sess.engine_id, sess.id) / META_NAME
        path.write_text(
            json.dumps(
                {
                    "id": sess.id,
                    "engine_id": sess.engine_id,
                    "dest_dir": sess.dest_dir,
                    "filename": sess.filename,
                    "size": sess.size,
                    "chunk_size": sess.chunk_size,
                    "jti": sess.jti,
                    "overwrite": sess.overwrite,
                    "cancel_at": sess.cancel_at,
                    "created_at": sess.created_at,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def load(self, engine_id: str, upload_id: str) -> UploadSession:
        path = self.session_dir(engine_id, upload_id) / META_NAME
        if not path.is_file():
            raise StorageError("upload session not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        return UploadSession(
            id=str(data["id"]),
            engine_id=str(data["engine_id"]),
            dest_dir=str(data["dest_dir"]),
            filename=str(data["filename"]),
            size=int(data["size"]),
            chunk_size=int(data["chunk_size"]),
            jti=str(data["jti"]),
            overwrite=bool(data.get("overwrite", False)),
            cancel_at=data.get("cancel_at"),
            created_at=float(data.get("created_at", time.time())),
        )

    def find_by_jti(self, engine_id: str, jti: str) -> UploadSession | None:
        base = self.staging_base(engine_id)
        if not base.is_dir():
            return None
        for child in base.iterdir():
            meta = child / META_NAME
            if not meta.is_file():
                continue
            try:
                sess = self.load(engine_id, child.name)
            except Exception:  # noqa: BLE001
                continue
            if sess.jti == jti and sess.cancel_at is None:
                return sess
        return None

    def received_chunks(self, sess: UploadSession) -> list[int]:
        cdir = self.session_dir(sess.engine_id, sess.id) / CHUNKS_DIR
        out: list[int] = []
        if not cdir.is_dir():
            return out
        for p in cdir.iterdir():
            if p.is_file() and p.name.isdigit():
                out.append(int(p.name))
        return sorted(out)

    def write_chunk(self, sess: UploadSession, index: int, data: bytes) -> None:
        if sess.cancel_at is not None:
            raise StorageError("upload cancelled")
        if index < 0 or (sess.chunk_count and index >= sess.chunk_count):
            raise StorageError("chunk index out of range")
        expected = sess.chunk_size
        if index == sess.chunk_count - 1 and sess.size > 0:
            expected = sess.size - index * sess.chunk_size
        if sess.size == 0:
            expected = 0
        if len(data) != expected:
            raise StorageError(f"chunk size mismatch: got {len(data)}, want {expected}")
        path = self.session_dir(sess.engine_id, sess.id) / CHUNKS_DIR / str(index)
        tmp = path.with_suffix(".partial")
        tmp.write_bytes(data)
        tmp.replace(path)

    def complete(self, sess: UploadSession) -> Path:
        if sess.cancel_at is not None:
            raise StorageError("upload cancelled")
        got = set(self.received_chunks(sess))
        need = set(range(sess.chunk_count))
        if got != need:
            missing = sorted(need - got)
            raise StorageError(f"missing chunks: {missing[:12]}")
        dest_dir = Path(sess.dest_dir)
        self._ensure_dest_dir(sess.engine_id, dest_dir)
        final = dest_dir / sess.filename
        if final.exists() and not sess.overwrite:
            raise StorageError("file exists; confirm overwrite")
        assembled = self.session_dir(sess.engine_id, sess.id) / "assembled"
        with assembled.open("wb") as out:
            for i in range(sess.chunk_count):
                part = self.session_dir(sess.engine_id, sess.id) / CHUNKS_DIR / str(i)
                out.write(part.read_bytes())
            if sess.size == 0:
                pass
        if assembled.stat().st_size != sess.size:
            assembled.unlink(missing_ok=True)
            raise StorageError("assembled size mismatch")
        # rename на том же томе
        if final.exists():
            final.unlink()
        assembled.replace(final)
        self._apply_perms(final, DEFAULT_CONTENT_MODE)
        shutil.rmtree(self.session_dir(sess.engine_id, sess.id), ignore_errors=True)
        return final

    @staticmethod
    def _content_owner() -> tuple[int, int]:
        """Всегда 1000:1000 (rudub / rudub2), переопределяется UPLOAD_CONTENT_UID/GID; -1 = не chown."""

        def _val(name: str, default: int) -> int:
            raw = os.getenv(name, "").strip()
            if raw == "":
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        return _val("UPLOAD_CONTENT_UID", DEFAULT_CONTENT_UID), _val(
            "UPLOAD_CONTENT_GID", DEFAULT_CONTENT_GID
        )

    @classmethod
    def _apply_perms(cls, path: Path, mode: int) -> None:
        """uid/gid 1000 + mode (файл 0644 / каталог 0755). Контейнер пишет от root."""
        uid, gid = cls._content_owner()
        chown = getattr(os, "chown", None)
        if chown is not None and (uid >= 0 or gid >= 0):
            try:
                chown(path, uid, gid)
            except OSError as exc:
                log.warning("chown %s -> %s:%s failed: %s", path, uid, gid, exc)
        try:
            os.chmod(path, mode)
        except OSError as exc:
            log.warning("chmod %s -> %o failed: %s", path, mode, exc)

    def _ensure_dest_dir(self, engine_id: str, dest_dir: Path) -> None:
        """Создать dest_dir и выставить 1000:1000 / 0755 на него и новые родители под root движка."""
        root = self.root_for(engine_id).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        cur = dest_dir.resolve()
        while True:
            try:
                cur.relative_to(root)
            except ValueError:
                break
            if cur == root:
                break
            self._apply_perms(cur, DEFAULT_DIR_MODE)
            cur = cur.parent

    def schedule_cancel(self, sess: UploadSession) -> UploadSession:
        sess.cancel_at = time.time() + self.gc_seconds
        self._write_meta(sess)
        return sess

    def gc_once(self) -> int:
        """Удалить просроченные cancel и слишком старые брошенные сессии (>24ч)."""
        removed = 0
        now = time.time()
        for engine_id, root in self.roots.items():
            base = root / ".upload-tmp"
            if not base.is_dir():
                continue
            for child in list(base.iterdir()):
                meta = child / META_NAME
                if not meta.is_file():
                    continue
                try:
                    sess = self.load(engine_id, child.name)
                except Exception:  # noqa: BLE001
                    continue
                stale = now - sess.created_at > 24 * 3600
                due = sess.cancel_at is not None and now >= float(sess.cancel_at)
                if due or stale:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
        return removed
