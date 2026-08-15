"""Staging + chunk assemble для upload sidecar."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "upload"))

from seeding_upload.storage import UploadStorage  # noqa: E402


def test_chunked_complete(tmp_path: Path):
    root = tmp_path / "b1"
    root.mkdir()
    dest = root / "inbox"
    dest.mkdir()
    st = UploadStorage({"b1": root}, chunk_size=4, gc_minutes=30)
    sess = st.create(
        upload_id="u1",
        engine_id="b1",
        dest_dir=str(dest),
        filename="x.bin",
        size=10,
        jti="j1",
        overwrite=False,
    )
    assert sess.chunk_count == 3
    st.write_chunk(sess, 0, b"abcd")
    st.write_chunk(sess, 1, b"efgh")
    st.write_chunk(sess, 2, b"ij")
    out = st.complete(sess)
    assert out.read_bytes() == b"abcdefghij"
    assert not (root / ".upload-tmp" / "u1").exists()


def test_resume_lists_received(tmp_path: Path):
    root = tmp_path / "b1"
    root.mkdir()
    dest = root / "d"
    dest.mkdir()
    st = UploadStorage({"b1": root}, chunk_size=5, gc_minutes=30)
    sess = st.create(
        upload_id="u2",
        engine_id="b1",
        dest_dir=str(dest),
        filename="y.bin",
        size=5,
        jti="j2",
        overwrite=False,
    )
    st.write_chunk(sess, 0, b"hello")
    assert st.received_chunks(sess) == [0]
