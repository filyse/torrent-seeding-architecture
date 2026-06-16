from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import httpx
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from seeding_desktop.config import load_api_base_url, load_api_key, update_config

# --- форматтеры ---------------------------------------------------------------


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


def _fmt_bytes(v: int | None) -> str:
    if v is None:
        return "—"
    n = float(v)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} TB"


def _fmt_ratio(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def _fmt_eta(sec: int | None) -> str:
    if sec is None or sec < 0:
        return "—"
    if sec == 0:
        return "0s"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# --- HTTP-клиент --------------------------------------------------------------


class ApiClient:
    """Тонкая обёртка над публичным REST API. Все запросы сериализованы локом,
    чтобы один httpx.Client можно было дёргать из UI-потока и фонового воркера."""

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self._lock = threading.Lock()
        self._build(base_url, api_key)

    def _build(self, base_url: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key.strip()
        headers = {"X-API-Key": self._key} if self._key else {}
        self._client = httpx.Client(base_url=self._base, timeout=30.0, headers=headers)

    def reconfigure(self, base_url: str, api_key: str) -> None:
        with self._lock:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._build(base_url, api_key)

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                msg = str(body["error"].get("message", resp.text))
            elif isinstance(body, dict) and "detail" in body:
                msg = str(body["detail"])
            else:
                msg = resp.text
        except ValueError:
            msg = resp.text
        raise RuntimeError(f"HTTP {resp.status_code}: {msg}")

    def _get(self, path: str) -> Any:
        with self._lock:
            r = self._client.get(path)
        self._raise_for_error(r)
        return r.json() if r.content else None

    def _post(self, path: str, *, json_body: Any = None, data: Any = None, files: Any = None) -> Any:
        with self._lock:
            r = self._client.post(path, json=json_body, data=data, files=files)
        self._raise_for_error(r)
        return r.json() if r.content else None

    def _delete(self, path: str) -> None:
        with self._lock:
            r = self._client.delete(path)
        self._raise_for_error(r)

    # чтение
    def list_torrents(self) -> list[dict[str, Any]]:
        return self._get("/api/v1/torrents") or []

    def session_stats(self) -> dict[str, Any]:
        return self._get("/api/v1/session/stats") or {}

    def list_engines(self) -> list[dict[str, Any]]:
        return self._get("/api/v1/engines") or []

    def list_labels(self) -> list[str]:
        return self._get("/api/v1/labels") or []

    def get_detail(self, tid: int) -> dict[str, Any]:
        return self._get(f"/api/v1/torrents/{tid}") or {}

    def list_files(self, tid: int) -> list[dict[str, Any]]:
        return self._get(f"/api/v1/torrents/{tid}/files") or []

    def list_trackers(self, tid: int) -> list[dict[str, Any]]:
        return self._get(f"/api/v1/torrents/{tid}/trackers") or []

    # добавление
    def add_magnet(self, magnet: str, *, engine_id: str, label: str, save_path: str, name: str) -> Any:
        body = {"magnet_uri": magnet.strip(), "label": label.strip(), "display_name": name.strip()}
        if save_path.strip():
            body["save_path"] = save_path.strip()
        else:
            body["engine_id"] = engine_id
        return self._post("/api/v1/torrents", json_body=body)

    def add_url(self, url: str, *, engine_id: str, label: str, save_path: str, name: str) -> Any:
        body = {"url": url.strip(), "label": label.strip(), "display_name": name.strip()}
        if save_path.strip():
            body["save_path"] = save_path.strip()
        else:
            body["engine_id"] = engine_id
        return self._post("/api/v1/torrents/url", json_body=body)

    def upload_files(self, paths: list[Path], *, engine_id: str, label: str, save_path: str) -> Any:
        data: dict[str, str] = {"label": label.strip()}
        if save_path.strip():
            data["save_path"] = save_path.strip()
        else:
            data["engine_id"] = engine_id
        if len(paths) == 1:
            p = paths[0]
            files = {"torrent_file": (p.name, p.read_bytes(), "application/x-bittorrent")}
            return self._post("/api/v1/torrents/upload", data=data, files=files)
        files_multi = [
            ("torrent_files", (p.name, p.read_bytes(), "application/x-bittorrent")) for p in paths
        ]
        return self._post("/api/v1/torrents/upload-batch", data=data, files=files_multi)

    # действия
    def bulk_pause(self, ids: list[int]) -> Any:
        return self._post("/api/v1/torrents/bulk/pause", json_body={"ids": ids})

    def bulk_resume(self, ids: list[int]) -> Any:
        return self._post("/api/v1/torrents/bulk/resume", json_body={"ids": ids})

    def bulk_label(self, ids: list[int], label: str) -> Any:
        return self._post("/api/v1/torrents/bulk/label", json_body={"ids": ids, "label": label})

    def bulk_delete(self, ids: list[int], delete_files: bool) -> Any:
        path = f"/api/v1/torrents/bulk/delete?delete_files={'true' if delete_files else 'false'}"
        return self._post(path, json_body={"ids": ids})

    def recheck(self, tid: int) -> Any:
        return self._post(f"/api/v1/torrents/{tid}/recheck")

    def reannounce(self, tid: int) -> Any:
        return self._post(f"/api/v1/torrents/{tid}/reannounce")


# --- фоновый снапшот ----------------------------------------------------------


class _SnapshotSignals(QObject):
    done = Signal(dict)
    error = Signal(str)


class _SnapshotTask(QRunnable):
    """Грузит список раздач + статистику в фоне, чтобы не морозить UI."""

    def __init__(self, api: ApiClient) -> None:
        super().__init__()
        self.api = api
        self.signals = _SnapshotSignals()

    def run(self) -> None:
        try:
            torrents = self.api.list_torrents()
            stats = self.api.session_stats()
            self.signals.done.emit({"torrents": torrents, "stats": stats})
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))


# --- диалог добавления --------------------------------------------------------


class AddDialog(QDialog):
    def __init__(self, parent: QWidget, api: ApiClient, engines: list[dict], labels: list[str]) -> None:
        super().__init__(parent)
        self.api = api
        self.setWindowTitle("Добавить торрент")
        self.resize(560, 320)
        self._files: list[Path] = []

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        # magnet
        tab_magnet = QWidget()
        fm = QFormLayout(tab_magnet)
        self.magnet = QLineEdit()
        self.magnet.setPlaceholderText("magnet:?xt=urn:btih:…")
        fm.addRow("Magnet", self.magnet)
        self.tabs.addTab(tab_magnet, "Magnet")

        # url
        tab_url = QWidget()
        fu = QFormLayout(tab_url)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…/file.torrent")
        fu.addRow("URL", self.url)
        self.tabs.addTab(tab_url, "URL")

        # files
        tab_file = QWidget()
        ff = QFormLayout(tab_file)
        row = QHBoxLayout()
        self.files_label = QLineEdit()
        self.files_label.setReadOnly(True)
        self.files_label.setPlaceholderText("Файлы не выбраны")
        btn_browse = QPushButton("Обзор…")
        btn_browse.clicked.connect(self.on_browse)
        row.addWidget(self.files_label, 1)
        row.addWidget(btn_browse)
        wrap = QWidget()
        wrap.setLayout(row)
        ff.addRow(".torrent", wrap)
        self.tabs.addTab(tab_file, "Файлы")

        layout.addWidget(self.tabs)

        # общие поля
        common = QFormLayout()
        self.engine = QComboBox()
        for e in engines:
            free = _fmt_bytes(e.get("disk_free"))
            total = _fmt_bytes(e.get("disk_total"))
            suffix = "" if e.get("online", True) else "  (офлайн)"
            self.engine.addItem(f"{e.get('id')} — своб. {free} из {total}{suffix}", e.get("id"))
        self.label = QComboBox()
        self.label.setEditable(True)
        self.label.addItem("")
        for lbl in labels:
            self.label.addItem(lbl)
        self.name = QLineEdit()
        self.name.setPlaceholderText("необязательно")
        self.custom_path = QLineEdit()
        self.custom_path.setPlaceholderText("оставьте пустым — путь берётся из движка")
        common.addRow("Движок", self.engine)
        common.addRow("Метка", self.label)
        common.addRow("Имя", self.name)
        common.addRow("Свой путь", self.custom_path)
        layout.addLayout(common)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def on_browse(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выбор .torrent", "", "Torrent files (*.torrent)"
        )
        if paths:
            self._files = [Path(p) for p in paths]
            self.files_label.setText("; ".join(p.name for p in self._files))

    def on_accept(self) -> None:
        engine_id = self.engine.currentData() or ""
        label = self.label.currentText()
        save_path = self.custom_path.text()
        name = self.name.text()
        try:
            tab = self.tabs.currentIndex()
            if tab == 0:
                magnet = self.magnet.text().strip()
                if not magnet:
                    raise ValueError("Укажите magnet-ссылку")
                self.api.add_magnet(
                    magnet, engine_id=engine_id, label=label, save_path=save_path, name=name
                )
            elif tab == 1:
                url = self.url.text().strip()
                if not url:
                    raise ValueError("Укажите URL")
                self.api.add_url(url, engine_id=engine_id, label=label, save_path=save_path, name=name)
            else:
                if not self._files:
                    raise ValueError("Выберите хотя бы один .torrent")
                res = self.api.upload_files(
                    self._files, engine_id=engine_id, label=label, save_path=save_path
                )
                if isinstance(res, dict) and res.get("failed"):
                    bad = ", ".join(i.get("filename", "?") for i in res.get("items", []) if not i.get("ok"))
                    QMessageBox.warning(
                        self, "Частичный успех", f"Добавлено {res.get('ok')}/{res.get('total')}.\nОшибки: {bad}"
                    )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", str(exc))
            return
        self.accept()


# --- диалог деталей -----------------------------------------------------------


class DetailsDialog(QDialog):
    def __init__(self, parent: QWidget, api: ApiClient, tid: int, name: str) -> None:
        super().__init__(parent)
        self.api = api
        self.tid = tid
        self.setWindowTitle(f"Раздача #{tid} — {name}")
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        self.files = QTableWidget(0, 4)
        self.files.setHorizontalHeaderLabels(["Файл", "Размер", "%", "Приоритет"])
        self.files.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.files, "Файлы")

        self.trackers = QTableWidget(0, 4)
        self.trackers.setHorizontalHeaderLabels(["Трекер", "Сообщение", "Пиры", "OK"])
        self.trackers.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.trackers, "Трекеры")
        layout.addWidget(tabs)

        actions = QHBoxLayout()
        btn_recheck = QPushButton("Перепроверить")
        btn_recheck.clicked.connect(self.on_recheck)
        btn_reannounce = QPushButton("Реанонс")
        btn_reannounce.clicked.connect(self.on_reannounce)
        btn_reload = QPushButton("Обновить")
        btn_reload.clicked.connect(self.reload)
        actions.addWidget(btn_recheck)
        actions.addWidget(btn_reannounce)
        actions.addStretch(1)
        actions.addWidget(btn_reload)
        layout.addLayout(actions)

        self.reload()

    def reload(self) -> None:
        try:
            files = self.api.list_files(self.tid)
        except Exception:  # noqa: BLE001
            files = []
        self.files.setRowCount(len(files))
        for i, f in enumerate(files):
            vals = [
                str(f.get("path", "")),
                _fmt_bytes(f.get("size")),
                _fmt_percent(f.get("progress")),
                str(f.get("priority", "")),
            ]
            for c, v in enumerate(vals):
                self.files.setItem(i, c, QTableWidgetItem(v))
        try:
            trackers = self.api.list_trackers(self.tid)
        except Exception:  # noqa: BLE001
            trackers = []
        self.trackers.setRowCount(len(trackers))
        for i, t in enumerate(trackers):
            vals = [
                str(t.get("url", "")),
                str(t.get("message", "")),
                str(t.get("num_peers", 0)),
                "✓" if t.get("verified") else "",
            ]
            for c, v in enumerate(vals):
                self.trackers.setItem(i, c, QTableWidgetItem(v))

    def on_recheck(self) -> None:
        try:
            self.api.recheck(self.tid)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", str(exc))

    def on_reannounce(self) -> None:
        try:
            self.api.reannounce(self.tid)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", str(exc))


# --- главное окно -------------------------------------------------------------

COLS = ["ID", "Имя", "Метка", "Статус", "%", "Размер", "↓", "↑", "Сиды/Пиры", "Ratio", "ETA", "Движок"]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Torrent Seeding Desktop")
        self.resize(1280, 760)
        self.api = ApiClient(load_api_base_url(), load_api_key())
        self.pool = QThreadPool.globalInstance()
        self._rows: list[dict[str, Any]] = []
        self._engines: list[dict[str, Any]] = []
        self._labels: list[str] = []
        self._busy = False
        self._last_n = -1

        root = QWidget(self)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        # подключение
        conn = QHBoxLayout()
        conn.addWidget(QLabel("API:"))
        self.api_url = QLineEdit(load_api_base_url())
        conn.addWidget(self.api_url, 2)
        conn.addWidget(QLabel("Ключ:"))
        self.api_key = QLineEdit(load_api_key())
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("X-API-Key (если задан)")
        conn.addWidget(self.api_key, 1)
        self.btn_connect = QPushButton("Подключить")
        self.btn_connect.clicked.connect(self.on_connect)
        conn.addWidget(self.btn_connect)
        outer.addLayout(conn)

        # сводка
        self.stats_label = QLabel("—")
        self.stats_label.setStyleSheet("padding:6px 2px; color:#555;")
        outer.addWidget(self.stats_label)

        # тулбар: поиск + фильтры + действия
        tb = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по имени, метке, hash…")
        self.search.textChanged.connect(self.render)
        tb.addWidget(self.search, 2)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Все статусы", "")
        for s in ("seeding", "downloading", "paused", "checking", "error"):
            self.status_filter.addItem(s, s)
        self.status_filter.currentIndexChanged.connect(self.render)
        tb.addWidget(self.status_filter)
        self.sort = QComboBox()
        for label, key in (("Сорт: новые", "id"), ("Имя", "name"), ("Размер", "size"), ("Ratio", "ratio")):
            self.sort.addItem(label, key)
        self.sort.currentIndexChanged.connect(self.render)
        tb.addWidget(self.sort)
        self.btn_add = QPushButton("+ Добавить")
        self.btn_add.clicked.connect(self.on_add)
        tb.addWidget(self.btn_add)
        self.btn_refresh = QPushButton("⟳")
        self.btn_refresh.setToolTip("Обновить")
        self.btn_refresh.clicked.connect(self.refresh)
        tb.addWidget(self.btn_refresh)
        outer.addLayout(tb)

        # таблица
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        # Убираем пунктирную focus-рамку и частичную заливку ячеек: выделяем строку целиком.
        self.table.setStyleSheet(
            "QTableView { outline: 0; }"
            "QTableView::item { border: 0; }"
            "QTableView::item:selected { background: #2f6fed; color: #ffffff; }"
        )
        self.table.doubleClicked.connect(self.on_details)
        outer.addWidget(self.table, 1)

        # действия над выбранными
        act = QHBoxLayout()
        self.sel_label = QLabel("Выбрано: 0")
        act.addWidget(self.sel_label)
        act.addStretch(1)
        for text, slot in (
            ("⏸ Пауза", self.on_pause),
            ("▶ Старт", self.on_resume),
            ("🏷 Метка", self.on_label),
            ("🗑 Удалить", self.on_remove),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        outer.addLayout(act)
        self.table.itemSelectionChanged.connect(self._sync_sel)

        self.statusBar().showMessage("Готово")
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        self.refresh_meta()
        self.refresh()

    # --- выбор ---
    def _selected_ids(self) -> list[int]:
        ids: list[int] = []
        for idx in self.table.selectionModel().selectedRows():
            item = self.table.item(idx.row(), 0)
            if item:
                ids.append(int(item.text()))
        return ids

    def _sync_sel(self) -> None:
        self.sel_label.setText(f"Выбрано: {len(self._selected_ids())}")

    def _handle_error(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 10000)

    # --- подключение ---
    def on_connect(self) -> None:
        base = self.api_url.text().strip()
        key = self.api_key.text().strip()
        self.api.reconfigure(base, key)
        update_config(api_base_url=base, api_key=key)
        self.refresh_meta()
        self.refresh()

    # --- данные ---
    def refresh_meta(self) -> None:
        """Движки и метки — для диалога добавления (синхронно, дёшево)."""
        try:
            self._engines = self.api.list_engines()
        except Exception:  # noqa: BLE001
            self._engines = []
        try:
            self._labels = self.api.list_labels()
        except Exception:  # noqa: BLE001
            self._labels = []

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        task = _SnapshotTask(self.api)
        task.signals.done.connect(self._on_snapshot)
        task.signals.error.connect(self._on_snapshot_error)
        self.pool.start(task)

    def _on_snapshot_error(self, msg: str) -> None:
        self._busy = False
        self._handle_error(msg)

    def _on_snapshot(self, snap: dict) -> None:
        self._busy = False
        self._rows = snap.get("torrents", [])
        self._render_stats(snap.get("stats", {}))
        self.render()
        self.statusBar().showMessage(f"Раздач: {len(self._rows)}", 3000)

    def _render_stats(self, s: dict) -> None:
        if not s:
            self.stats_label.setText("Статистика недоступна")
            return
        eng = ""
        if s.get("engines_total") is not None:
            eng = f"   ·   {s.get('engines_ok', 0)}/{s.get('engines_total')} движков"
        self.stats_label.setText(
            f"Раздач: {s.get('torrents', 0)} ({s.get('torrents_active', 0)} актив.)"
            f"    ↓ {_fmt_rate(s.get('download_rate'))}    ↑ {_fmt_rate(s.get('upload_rate'))}"
            f"    Отдано: {_fmt_bytes(s.get('total_uploaded'))}{eng}"
        )

    def render(self) -> None:
        q = self.search.text().strip().lower()
        sf = self.status_filter.currentData()
        rows = list(self._rows)
        if q:
            def match(t: dict) -> bool:
                hay = " ".join(
                    str(t.get(k, "")) for k in ("display_name", "label", "info_hash")
                ).lower()
                return q in hay
            rows = [t for t in rows if match(t)]
        if sf:
            rows = [t for t in rows if t.get("status") == sf]

        key = self.sort.currentData()
        if key == "name":
            rows.sort(key=lambda t: str(t.get("display_name", "")).lower())
        elif key == "size":
            rows.sort(key=lambda t: (t.get("runtime") or {}).get("size") or 0, reverse=True)
        elif key == "ratio":
            rows.sort(key=lambda t: (t.get("runtime") or {}).get("ratio") or 0.0, reverse=True)
        else:
            rows.sort(key=lambda t: t.get("id", 0), reverse=True)

        selected = set(self._selected_ids())
        self.table.setRowCount(len(rows))
        for i, t in enumerate(rows):
            rt = t.get("runtime") or {}
            name = str(t.get("display_name", ""))
            if name.lower().endswith(".torrent"):
                name = name[: -len(".torrent")]
            cells = [
                str(t.get("id", "")),
                name,
                str(t.get("label", "")),
                str(t.get("status", "")),
                _fmt_percent(rt.get("progress")),
                _fmt_bytes(rt.get("size")),
                _fmt_rate(rt.get("download_rate")),
                _fmt_rate(rt.get("upload_rate")),
                f"{rt.get('num_seeds', 0)}/{rt.get('peers', 0)}",
                _fmt_ratio(rt.get("ratio")),
                _fmt_eta(rt.get("eta")),
                str(t.get("engine_id", "")),
            ]
            for c, v in enumerate(cells):
                item = QTableWidgetItem(v)
                if c == 1:
                    item.setToolTip(str(t.get("display_name", "")))
                self.table.setItem(i, c, item)
        # Восстанавливаем выделение по id (автообновление не должно его сбрасывать).
        if selected:
            self.table.clearSelection()
            for i in range(self.table.rowCount()):
                it = self.table.item(i, 0)
                if it and it.text().isdigit() and int(it.text()) in selected:
                    self.table.selectRow(i)
        # Ширину колонок подгоняем только при изменении числа строк, а не каждые 5с.
        if self.table.rowCount() != self._last_n:
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self._last_n = self.table.rowCount()
        self._sync_sel()

    # --- действия ---
    def on_add(self) -> None:
        self.refresh_meta()
        dlg = AddDialog(self, self.api, self._engines, self._labels)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh_meta()
            self.refresh()

    def on_details(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        tid = ids[0]
        name = ""
        for t in self._rows:
            if t.get("id") == tid:
                name = str(t.get("display_name", ""))
                break
        DetailsDialog(self, self.api, tid, name).exec()

    def _bulk(self, fn, *args) -> None:  # noqa: ANN001
        ids = self._selected_ids()
        if not ids:
            return
        try:
            fn(ids, *args)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", str(exc))
            return
        self.refresh()

    def on_pause(self) -> None:
        self._bulk(self.api.bulk_pause)

    def on_resume(self) -> None:
        self._bulk(self.api.bulk_resume)

    def on_label(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self, "Метка", "Метка для выбранных (пусто — снять):")
        if not ok:
            return
        self._bulk(self.api.bulk_label, text.strip())

    def on_remove(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Удаление")
        box.setText(f"Удалить выбранные раздачи ({len(ids)})?")
        cb = QCheckBox("Также удалить файлы с диска")
        box.setCheckBox(cb)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._bulk(self.api.bulk_delete, cb.isChecked())


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
