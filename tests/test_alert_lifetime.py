"""Алерты libtorrent нельзя трогать после следующего pop_alerts().

Причина SIGSEGV движков (a2, октябрь 2026): piece_finished_alert держали в
списке «side» и разбирали после новых pop_alerts — чтение освобождённой памяти.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from seeding_engine import fastresume_io as fio
from seeding_engine.fastresume_io import PieceDone, pop_alert_events, save_resume_data_blocking


class _Dead(Exception):
    pass


class _Alert:
    """Алерт, который «умирает» на следующем pop_alerts, как в libtorrent."""

    def __init__(self, **fields):
        object.__setattr__(self, "_fields", fields)
        object.__setattr__(self, "_alive", True)

    def __getattr__(self, name):
        if not object.__getattribute__(self, "_alive"):
            raise _Dead(f"use-after-free: {name}")
        try:
            return object.__getattribute__(self, "_fields")[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class PieceFinished(_Alert):
    pass


class ResumeAlert(_Alert):
    pass


class ResumeFailed(_Alert):
    pass


class _Handle:
    def __init__(self, key):
        self.key = key

    def __eq__(self, other):
        return isinstance(other, _Handle) and other.key == self.key

    def __hash__(self):
        return hash(self.key)

    def info_hashes(self):
        return SimpleNamespace(v1=self.key, v2=None)

    def save_resume_data(self, *_a):
        pass

    def is_valid(self):
        return True


class _Session:
    def __init__(self, batches):
        self.batches = list(batches)
        self.last: list = []

    def wait_for_alert(self, _ms):
        return None

    def pop_alerts(self):
        for a in self.last:
            object.__setattr__(a, "_alive", False)
        self.last = self.batches.pop(0) if self.batches else []
        return list(self.last)


_LT = SimpleNamespace(
    piece_finished_alert=PieceFinished,
    save_resume_data_alert=ResumeAlert,
    save_resume_data_failed_alert=ResumeFailed,
    write_resume_data_buf=lambda params: b"blob",
)


def test_pop_alert_events_returns_snapshots_only():
    h = _Handle("a" * 40)
    ses = _Session([[PieceFinished(handle=h, piece_index=3), _Alert(msg="x")], []])
    events = pop_alert_events(_LT, ses)
    ses.pop_alerts()  # первые алерты умерли
    assert events == [PieceDone(h, 3)]
    assert events[0].handle is h and events[0].piece == 3


def test_save_resume_side_survives_later_pops(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEDING_FASTRESUME_DIR", str(tmp_path))
    h1, h2 = _Handle("1" * 40), _Handle("2" * 40)
    ses = _Session([
        [PieceFinished(handle=h2, piece_index=7)],
        [ResumeAlert(handle=h1, params=object())],
    ])
    saved, side = save_resume_data_blocking(_LT, ses, {10: h1}, timeout=2.0)
    ses.pop_alerts()
    assert saved == 1
    assert (tmp_path / "10.fastresume").read_bytes() == b"blob"
    # раньше side содержал сами алерты: обращение к ним — use-after-free
    assert side == [PieceDone(h2, 7)]


def test_account_piece_alerts_uses_snapshots(monkeypatch):
    from seeding_engine import torrent_runtime as tr

    added = []
    h = _Handle("3" * 40)
    h.status = lambda: SimpleNamespace(state="checking_files")
    monkeypatch.setattr(tr, "_state_label", lambda lt, st: st.state)
    monkeypatch.setattr(tr, "is_full_hash_check_state", lambda s: s == "checking_files")
    monkeypatch.setattr(tr, "_piece_bytes", lambda handle, piece: 16384 * (piece + 1))
    fake = SimpleNamespace(
        _lt=_LT, _handles={5: h},
        _check_rate=SimpleNamespace(add=lambda db_id, n, now: added.append((db_id, n))),
    )
    tr.LibtorrentTorrentRuntime._account_piece_alerts(fake, [PieceDone(h, 1), PieceDone(_Handle("x"), 0)])
    assert added == [(5, 32768)]


def test_no_raw_pop_alerts_outside_alert_lock():
    """pop_alerts() вызывается только в fastresume_io, под ALERT_LOCK."""
    root = Path(fio.__file__).parent
    offenders = [
        p.name for p in root.glob("*.py")
        if p.name != "fastresume_io.py" and "pop_alerts(" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


_STRESS = Path(__file__).with_name("lt_alert_stress.py")


def _run_stress(mode: str, seconds: float) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, str(_STRESS), mode, str(seconds)],
        capture_output=True, text=True, timeout=seconds + 120, env=env,
    )


def test_stress_real_libtorrent_no_segfault():
    pytest.importorskip("libtorrent")
    r = _run_stress("fixed", float(os.getenv("SEEDING_LT_STRESS_SECONDS", "20")))
    assert r.returncode == 0, (r.returncode, r.stderr[-2000:])
    assert "survived fixed" in r.stdout


@pytest.mark.skipif(os.getenv("SEEDING_LT_REPRO") != "1", reason="демонстрация краша, SEEDING_LT_REPRO=1")
def test_stress_old_pattern_segfaults():
    pytest.importorskip("libtorrent")
    r = _run_stress("buggy", 20)
    assert r.returncode == -signal.SIGSEGV, (r.returncode, r.stdout, r.stderr[-1000:])


_ENGINE_STRESS = Path(__file__).with_name("lt_engine_stress.py")


def test_engine_stress_add_remove_recheck_no_segfault():
    """Настоящий LibtorrentTorrentRuntime под пачками add/remove/recheck/pause.

    На 1.6.13.1 падает SIGSEGV за доли секунды, после фикса — нет."""
    pytest.importorskip("libtorrent")
    seconds = float(os.getenv("SEEDING_LT_STRESS_SECONDS", "20"))
    r = subprocess.run(
        [sys.executable, str(_ENGINE_STRESS), str(seconds)],
        capture_output=True, text=True, timeout=seconds + 120, env=dict(os.environ),
    )
    assert r.returncode == 0, (r.returncode, r.stderr[-2000:])
    assert "survived" in r.stdout
