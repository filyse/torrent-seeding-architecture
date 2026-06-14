from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from seeding_desktop.config import load_api_base_url


def _fmt_percent(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{max(0.0, min(100.0, v * 100.0)):.1f}%"


def _fmt_rate(v: int | None) -> str:
    if not v:
        return "0 KB/s"
    kb = v / 1024.0
    if kb < 1024:
        return f"{kb:.1f} KB/s"
    return f"{kb / 1024.0:.2f} MB/s"


class ApiClient:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base, timeout=30.0)

    def set_base_url(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base, timeout=30.0)

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                msg = str(body["error"].get("message", resp.text))
            else:
                msg = resp.text
        except ValueError:
            msg = resp.text
        raise RuntimeError(f"HTTP {resp.status_code}: {msg}")

    def list_torrents(self) -> list[dict[str, Any]]:
        r = self._client.get("/api/v1/torrents")
        self._raise_for_error(r)
        return r.json()

    def add_magnet(self, magnet_uri: str, save_path: str, display_name: str) -> dict[str, Any]:
        r = self._client.post(
            "/api/v1/torrents",
            json={
                "magnet_uri": magnet_uri.strip(),
                "save_path": save_path.strip(),
                "display_name": display_name.strip(),
            },
        )
        self._raise_for_error(r)
        return r.json()

    def add_file(self, torrent_file: Path, save_path: str, display_name: str) -> dict[str, Any]:
        payload = torrent_file.read_bytes()
        r = self._client.post(
            "/api/v1/torrents/upload",
            data={"save_path": save_path.strip(), "display_name": display_name.strip()},
            files={"torrent_file": (torrent_file.name, payload, "application/x-bittorrent")},
        )
        self._raise_for_error(r)
        return r.json()

    def pause(self, torrent_id: int) -> None:
        r = self._client.post(f"/api/v1/torrents/{torrent_id}/pause")
        self._raise_for_error(r)

    def resume(self, torrent_id: int) -> None:
        r = self._client.post(f"/api/v1/torrents/{torrent_id}/resume")
        self._raise_for_error(r)

    def remove(self, torrent_id: int) -> None:
        r = self._client.delete(f"/api/v1/torrents/{torrent_id}")
        self._raise_for_error(r)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Torrent Seeding Desktop")
        self.resize(1200, 700)
        self.api = ApiClient(load_api_base_url())

        root = QWidget(self)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        row_top = QHBoxLayout()
        row_top.addWidget(QLabel("API URL:"))
        self.api_url = QLineEdit(load_api_base_url())
        row_top.addWidget(self.api_url, 1)
        self.btn_connect = QPushButton("Подключить")
        self.btn_refresh = QPushButton("Обновить")
        row_top.addWidget(self.btn_connect)
        row_top.addWidget(self.btn_refresh)
        outer.addLayout(row_top)

        form = QGridLayout()
        self.magnet = QLineEdit()
        self.magnet.setPlaceholderText("magnet:?xt=urn:btih:...")
        self.save_path = QLineEdit("/data")
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("необязательно")
        self.torrent_file = QLineEdit()
        self.torrent_file.setPlaceholderText("Выберите .torrent файл")
        self.btn_browse = QPushButton("Обзор...")
        self.btn_add_magnet = QPushButton("Добавить magnet")
        self.btn_add_file = QPushButton("Загрузить .torrent")

        form.addWidget(QLabel("Magnet URI"), 0, 0)
        form.addWidget(self.magnet, 0, 1, 1, 3)
        form.addWidget(QLabel("Save path"), 1, 0)
        form.addWidget(self.save_path, 1, 1, 1, 3)
        form.addWidget(QLabel("Display name"), 2, 0)
        form.addWidget(self.display_name, 2, 1, 1, 3)
        form.addWidget(QLabel(".torrent file"), 3, 0)
        form.addWidget(self.torrent_file, 3, 1, 1, 2)
        form.addWidget(self.btn_browse, 3, 3)
        form.addWidget(self.btn_add_magnet, 4, 2)
        form.addWidget(self.btn_add_file, 4, 3)
        outer.addLayout(form)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["id", "имя", "magnet", "save_path", "статус", "прогресс", "DL", "UL", "пиры", "info_hash"]
        )
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        outer.addWidget(self.table, 1)

        row_actions = QHBoxLayout()
        self.btn_pause = QPushButton("Пауза")
        self.btn_resume = QPushButton("Старт")
        self.btn_remove = QPushButton("Удалить")
        row_actions.addWidget(self.btn_pause)
        row_actions.addWidget(self.btn_resume)
        row_actions.addWidget(self.btn_remove)
        row_actions.addStretch(1)
        outer.addLayout(row_actions)

        self.statusBar().showMessage("Готово")
        self.timer = QTimer(self)
        self.timer.setInterval(8000)
        self.timer.timeout.connect(self.reload_table)
        self.timer.start()

        self.btn_connect.clicked.connect(self.on_connect)
        self.btn_refresh.clicked.connect(self.reload_table)
        self.btn_browse.clicked.connect(self.on_browse)
        self.btn_add_magnet.clicked.connect(self.on_add_magnet)
        self.btn_add_file.clicked.connect(self.on_add_file)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_resume.clicked.connect(self.on_resume)
        self.btn_remove.clicked.connect(self.on_remove)

        self.reload_table()

    def _current_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def _handle_error(self, exc: Exception) -> None:
        QMessageBox.critical(self, "Ошибка", str(exc))
        self.statusBar().showMessage(str(exc), 10000)

    def on_connect(self) -> None:
        try:
            self.api.set_base_url(self.api_url.text().strip())
            self.reload_table()
        except Exception as exc:  # noqa: BLE001
            self._handle_error(exc)

    def on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выбор .torrent", "", "Torrent files (*.torrent)")
        if path:
            self.torrent_file.setText(path)

    def on_add_magnet(self) -> None:
        magnet = self.magnet.text().strip()
        if not magnet:
            self.statusBar().showMessage("Укажите magnet URI", 5000)
            return
        try:
            self.api.add_magnet(magnet, self.save_path.text(), self.display_name.text())
            self.magnet.clear()
            self.display_name.clear()
            self.reload_table()
        except Exception as exc:  # noqa: BLE001
            self._handle_error(exc)

    def on_add_file(self) -> None:
        raw = self.torrent_file.text().strip()
        if not raw:
            self.statusBar().showMessage("Выберите .torrent файл", 5000)
            return
        p = Path(raw)
        if not p.is_file():
            self.statusBar().showMessage("Файл не найден", 5000)
            return
        try:
            self.api.add_file(p, self.save_path.text(), self.display_name.text())
            self.display_name.clear()
            self.reload_table()
        except Exception as exc:  # noqa: BLE001
            self._handle_error(exc)

    def on_pause(self) -> None:
        torrent_id = self._current_id()
        if torrent_id is None:
            return
        try:
            self.api.pause(torrent_id)
            self.reload_table()
        except Exception as exc:  # noqa: BLE001
            self._handle_error(exc)

    def on_resume(self) -> None:
        torrent_id = self._current_id()
        if torrent_id is None:
            return
        try:
            self.api.resume(torrent_id)
            self.reload_table()
        except Exception as exc:  # noqa: BLE001
            self._handle_error(exc)

    def on_remove(self) -> None:
        torrent_id = self._current_id()
        if torrent_id is None:
            return
        try:
            self.api.remove(torrent_id)
            self.reload_table()
        except Exception as exc:  # noqa: BLE001
            self._handle_error(exc)

    def reload_table(self) -> None:
        try:
            rows = self.api.list_torrents()
        except Exception as exc:  # noqa: BLE001
            self._handle_error(exc)
            return
        self.table.setRowCount(len(rows))
        for i, t in enumerate(rows):
            runtime = t.get("runtime") or {}
            vals = [
                str(t.get("id", "")),
                str(t.get("display_name", "")),
                str(t.get("magnet_uri", "—") or "—"),
                str(t.get("save_path", "")),
                str(t.get("status", "")),
                _fmt_percent(runtime.get("progress")),
                _fmt_rate(runtime.get("download_rate")),
                _fmt_rate(runtime.get("upload_rate")),
                str(runtime.get("peers", "—")),
                str(runtime.get("info_hash", "—") or "—"),
            ]
            for col, v in enumerate(vals):
                self.table.setItem(i, col, QTableWidgetItem(v))
        self.statusBar().showMessage(f"Загружено записей: {len(rows)}", 3000)


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
