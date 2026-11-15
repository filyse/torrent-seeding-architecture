"""Диск раздачи и сверка размеров: пустой каталог не должен выглядеть как библиотека."""

from pathlib import Path

import pytest
from seeding_engine.storage_guard import (
    StorageNotMounted,
    assert_content_mounted,
    files_cover,
    mount_problem,
)


def test_missing_directory_is_not_a_mount(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    problem = mount_problem(root, "c1")
    assert problem is not None
    assert "не смонтирован" in problem


def test_same_filesystem_is_not_a_mount(tmp_path: Path):
    root = tmp_path / "data"
    (root / "c1").mkdir(parents=True)
    problem = mount_problem(root, "c1")
    assert problem is not None
    assert "тот же filesystem" in problem


def test_other_device_is_a_mount(tmp_path: Path):
    root = tmp_path / "data"
    (root / "c1").mkdir(parents=True)
    problem = mount_problem(root, "c1", device=lambda path: 7 if path.name == "c1" else 1)
    assert problem is None


def test_require_mount_off_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SEEDING_STORAGE_REQUIRE_MOUNT", raising=False)
    assert_content_mounted(tmp_path, "missing")


def test_require_mount_on_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEEDING_STORAGE_REQUIRE_MOUNT", "1")
    root = tmp_path / "data"
    root.mkdir()
    with pytest.raises(StorageNotMounted):
        assert_content_mounted(root, "c1")


def test_files_cover_when_every_file_is_full(tmp_path: Path):
    show = tmp_path / "Show"
    show.mkdir()
    (show / "a.mkv").write_bytes(b"abcd")
    (show / "b.mkv").write_bytes(b"abcdef")
    assert files_cover(tmp_path, [("Show/a.mkv", 4), ("Show/b.mkv", 6)])


def test_files_cover_rejects_a_short_file(tmp_path: Path):
    (tmp_path / "a.mkv").write_bytes(b"ab")
    assert files_cover(tmp_path, [("a.mkv", 4)]) is False


def test_files_cover_rejects_escape(tmp_path: Path):
    assert files_cover(tmp_path, [("../outside.mkv", 1)]) is False
    assert files_cover(tmp_path, []) is False
