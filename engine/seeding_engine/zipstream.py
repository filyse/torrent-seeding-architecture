"""Потоковый ZIP (STORE, без сжатия) с заранее известной длиной.

Без временных файлов: заголовки строятся на лету, данные читаются с диска
кусками. CRC32 считается по ходу и пишется в data descriptor (бит 3), поэтому
длина архива не зависит от CRC и известна до начала отдачи (Content-Length).
ZIP64 — только для записей/смещений, которые не влезают в 32 бита.
Имена — UTF-8 (бит 11).
"""

from __future__ import annotations

import struct
import time
import zlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

U32 = 0xFFFFFFFF
U16 = 0xFFFF
FLAGS = 0x0008 | 0x0800  # data descriptor + UTF-8
VERSION_ZIP64 = 45
VERSION_PLAIN = 20


@dataclass
class ZipEntry:
    name: str  # путь внутри архива, через '/'
    size: int
    mtime: float
    path: object = None  # что передать в opener
    # заполняется при раскладке
    offset: int = 0
    crc: int = 0
    zip64: bool = False
    _name_b: bytes = field(default=b"", repr=False)


def _dos_time(ts: float) -> tuple[int, int]:
    t = time.localtime(ts if ts and ts > 315532800 else 315532800)
    year = max(1980, min(2107, t.tm_year))
    dtime = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
    ddate = ((year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
    return dtime, ddate


class ZipPlan:
    def __init__(self, entries: list[ZipEntry]):
        self.entries = entries
        off = 0
        for e in entries:
            e._name_b = e.name.encode("utf-8")
            e.offset = off
            e.zip64 = e.size >= U32 or off >= U32
            off += len(self._local(e)) + e.size + (24 if e.zip64 else 16)
        self.cd_offset = off
        self.cd_size = sum(len(self._central(e)) for e in entries)
        self.zip64_end = (
            len(entries) >= U16 or self.cd_offset >= U32 or self.cd_size >= U32
        )
        self.total = off + self.cd_size + len(self._end())

    # -- заголовки -------------------------------------------------------
    def _local(self, e: ZipEntry) -> bytes:
        dtime, ddate = _dos_time(e.mtime)
        extra = struct.pack("<HHQQ", 1, 16, 0, 0) if e.zip64 else b""
        sz = U32 if e.zip64 else 0
        return (
            struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                VERSION_ZIP64 if e.zip64 else VERSION_PLAIN,
                FLAGS, 0, dtime, ddate, 0, sz, sz,
                len(e._name_b), len(extra),
            )
            + e._name_b
            + extra
        )

    @staticmethod
    def _descriptor(e: ZipEntry) -> bytes:
        if e.zip64:
            return struct.pack("<IIQQ", 0x08074B50, e.crc, e.size, e.size)
        return struct.pack("<IIII", 0x08074B50, e.crc, e.size, e.size)

    def _central(self, e: ZipEntry) -> bytes:
        dtime, ddate = _dos_time(e.mtime)
        vals = []
        size32 = e.size
        off32 = e.offset
        if e.size >= U32:
            vals += [e.size, e.size]
            size32 = U32
        if e.offset >= U32:
            vals.append(e.offset)
            off32 = U32
        extra = struct.pack("<HH", 1, 8 * len(vals)) + struct.pack(f"<{len(vals)}Q", *vals) if vals else b""
        ver = VERSION_ZIP64 if e.zip64 else VERSION_PLAIN
        return (
            struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50, (3 << 8) | ver, ver, FLAGS, 0, dtime, ddate,
                e.crc, size32, size32, len(e._name_b), len(extra), 0, 0, 0,
                0o100644 << 16, off32,
            )
            + e._name_b
            + extra
        )

    def _end(self) -> bytes:
        n = len(self.entries)
        out = b""
        if self.zip64_end:
            z64_off = self.cd_offset + self.cd_size
            out += struct.pack(
                "<IQHHIIQQQQ", 0x06064B50, 44, VERSION_ZIP64, VERSION_ZIP64, 0, 0,
                n, n, self.cd_size, self.cd_offset,
            )
            out += struct.pack("<IIQI", 0x07064B50, 0, z64_off, 1)
        out += struct.pack(
            "<IHHHHIIH", 0x06054B50, 0, 0, min(n, U16), min(n, U16),
            min(self.cd_size, U32), min(self.cd_offset, U32), 0,
        )
        return out

    # -- поток -----------------------------------------------------------
    async def stream(
        self,
        read_chunks: Callable[[ZipEntry], AsyncIterator[bytes]],
    ) -> AsyncIterator[bytes]:
        """read_chunks(entry) должен отдать ровно entry.size байт."""
        for e in self.entries:
            yield self._local(e)
            crc = 0
            got = 0
            async for chunk in read_chunks(e):
                take = chunk[: e.size - got]
                if not take:
                    break
                crc = zlib.crc32(take, crc)
                got += len(take)
                yield take
            if got != e.size:
                # файл укоротился во время отдачи — длину уже обещали; рвём поток
                raise OSError(f"file changed while zipping: {e.name}")
            e.crc = crc & U32
            yield self._descriptor(e)
        for e in self.entries:
            yield self._central(e)
        yield self._end()
