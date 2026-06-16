import "./style.css";

const API = "/api/v1";

/** Интервал опроса: активная загрузка / есть торренты / пусто */
const POLL_MS = { active: 2_000, idle: 6_000, empty: 30_000 } as const;

type TorrentOut = {
  id: number;
  info_hash: string | null;
  magnet_uri: string | null;
  display_name: string;
  save_path: string;
  engine_id: string;
  label: string;
  status: string;
  created_at: string;
  runtime?: RuntimeOut | null;
};

type SessionStats = {
  torrents: number;
  torrents_active: number;
  download_rate: number;
  upload_rate: number;
  total_uploaded: number;
  total_downloaded: number;
  engines_ok?: number;
  engines_total?: number;
};

type BatchUploadItem = {
  filename: string;
  ok: boolean;
  id?: number | null;
  display_name?: string | null;
  error?: string | null;
};

type BatchUploadResult = {
  total: number;
  ok: number;
  failed: number;
  items: BatchUploadItem[];
};

type EngineOut = {
  id: string;
  url: string;
  storage_prefix: string;
  listen_port: number | null;
  disk_total?: number | null;
  disk_free?: number | null;
  online?: boolean;
};

type RuntimeOut = {
  db_id: number;
  magnet_uri: string | null;
  save_path: string;
  runtime_status: string;
  info_hash: string | null;
  progress: number | null;
  lt_state: string | null;
  download_rate: number | null;
  upload_rate: number | null;
  total_uploaded: number | null;
  peers: number | null;
  name?: string | null;
  size?: number | null;
  downloaded?: number | null;
  num_seeds?: number | null;
  ratio?: number | null;
  eta?: number | null;
  added_time?: number | null;
  download_limit?: number | null;
  upload_limit?: number | null;
};

type TorrentFileOut = {
  index: number;
  path: string;
  size: number;
  downloaded: number;
  progress: number;
  priority: number;
};

type TorrentTrackerOut = {
  url: string;
  tier: number;
  message: string;
  verified: boolean;
  num_peers: number;
};

type TorrentPeerOut = {
  endpoint: string;
  client: string | null;
  progress: number | null;
  download_rate: number | null;
  upload_rate: number | null;
  flags: string | null;
  source: string | null;
};

type TorrentDetailOut = TorrentOut & { runtime: RuntimeOut | null; peer_list?: TorrentPeerOut[] };
type Route = { view: "list" } | { view: "detail"; id: number } | { view: "settings" };
type DeleteTorrentChoice = "cancel" | "torrent_only" | "torrent_and_files";

let listPollTimer: ReturnType<typeof setTimeout> | null = null;
let listStream: EventSource | null = null;
let detailPollTimer: ReturnType<typeof setTimeout> | null = null;
let listAbort: AbortController | null = null;
let detailAbort: AbortController | null = null;
let listLoadGeneration = 0;
let lastListItems: TorrentOut[] = [];
let toastTimer: ReturnType<typeof setTimeout> | null = null;
let selectedIds = new Set<number>();
let selectionChanged: (() => void) | null = null;

function lsGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function lsSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore (private mode / quota) */
  }
}

type ListSort = "name" | "progress" | "up" | "added";
type ListDensity = "comfortable" | "compact";
type ThemeMode = "auto" | "light" | "dark";

let listSearch = lsGet("ui.search") ?? "";
let listStatusFilter = lsGet("ui.status") ?? "";
let listLabelFilter = lsGet("ui.label") ?? "";
let listSort: ListSort = ((): ListSort => {
  const v = lsGet("ui.sort");
  return v === "name" || v === "progress" || v === "up" || v === "added" ? v : "added";
})();
let listDensity: ListDensity = lsGet("ui.density") === "compact" ? "compact" : "comfortable";

function getThemeMode(): ThemeMode {
  const v = lsGet("ui.theme");
  return v === "light" || v === "dark" ? v : "auto";
}
function applyTheme(mode: ThemeMode): void {
  const root = document.documentElement;
  if (mode === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", mode);
  lsSet("ui.theme", mode);
}

type DetailSpoilerKey = "files" | "trackers" | "peers" | "meta";
const detailSpoilerOpenById = new Map<number, Record<DetailSpoilerKey, boolean>>();

function getDetailSpoilerState(torrentId: number): Record<DetailSpoilerKey, boolean> {
  return (
    detailSpoilerOpenById.get(torrentId) ?? { files: false, trackers: false, peers: false, meta: false }
  );
}

function saveDetailSpoilerStateFromDom(container: HTMLElement, torrentId: number): void {
  const cur = getDetailSpoilerState(torrentId);
  const read = (key: DetailSpoilerKey) =>
    (container.querySelector(`details[data-spoiler="${key}"]`) as HTMLDetailsElement | null)?.open ??
    cur[key];
  detailSpoilerOpenById.set(torrentId, {
    files: read("files"),
    trackers: read("trackers"),
    peers: read("peers"),
    meta: read("meta"),
  });
}

function applyDetailSpoilerState(el: HTMLDetailsElement, torrentId: number, key: DetailSpoilerKey): void {
  el.dataset.spoiler = key;
  const state = getDetailSpoilerState(torrentId);
  el.open = state[key];
  el.addEventListener("toggle", () => {
    const next = { ...getDetailSpoilerState(torrentId) };
    next[key] = el.open;
    detailSpoilerOpenById.set(torrentId, next);
  });
}

type ListHostRefs = {
  listEl: HTMLElement;
  countEl: HTMLElement;
  metaEl: HTMLElement;
  scheduleNext: (items: TorrentOut[], opts?: { fast?: boolean }) => void;
};

function stopListPoll(): void {
  if (listPollTimer !== null) {
    clearTimeout(listPollTimer);
    listPollTimer = null;
  }
}

function upsertTorrentInList(torrent: TorrentOut): TorrentOut[] {
  const idx = lastListItems.findIndex((t) => t.id === torrent.id);
  if (idx >= 0) {
    const next = [...lastListItems];
    next[idx] = torrent;
    lastListItems = next;
  } else {
    lastListItems = [torrent, ...lastListItems];
  }
  return lastListItems;
}

function stopListStream(): void {
  if (listStream !== null) {
    listStream.close();
    listStream = null;
  }
}

function clearViewPolls(): void {
  stopListStream();
  if (listPollTimer !== null) {
    clearTimeout(listPollTimer);
    listPollTimer = null;
  }
  if (detailPollTimer !== null) {
    clearTimeout(detailPollTimer);
    detailPollTimer = null;
  }
  listAbort?.abort();
  listAbort = null;
  detailAbort?.abort();
  detailAbort = null;
}

function apiHeaders(json = true): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  try {
    const key = localStorage.getItem("seedingApiKey");
    if (key) h["X-API-Key"] = key;
  } catch {
    /* ignore */
  }
  return h;
}

async function throwIfNotOk(res: Response): Promise<void> {
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("Нужен API-ключ (localStorage.seedingApiKey)");
    }
    if (res.status === 403) {
      throw new Error("Доступ запрещён");
    }
    const text = await res.text();
    let detail = text || res.statusText;
    try {
      const body = JSON.parse(text) as { detail?: unknown; error?: { message?: string } };
      if (body.error?.message !== undefined) detail = String(body.error.message);
      else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
    } catch {
      /* text */
    }
    throw new Error(detail);
  }
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { ...apiHeaders(), ...init?.headers },
  });
  await throwIfNotOk(res);
  return res.json() as Promise<T>;
}

async function fetchDelete(path: string, deleteFiles = false): Promise<void> {
  const q = deleteFiles ? "?delete_files=true" : "";
  const res = await fetch(`${API}${path}${q}`, {
    method: "DELETE",
    headers: apiHeaders(false),
  });
  await throwIfNotOk(res);
}

function showToast(message: string, isError = false): void {
  if (toastTimer !== null) clearTimeout(toastTimer);
  document.querySelector(".toast")?.remove();
  const t = el("div", {
    className: `toast${isError ? " toast--error" : ""}`,
    role: "status",
  });
  t.textContent = message;
  document.body.append(t);
  toastTimer = setTimeout(() => t.remove(), isError ? 5000 : 2500);
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Record<string, string> = {},
  children: (string | Node)[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "className") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(typeof c === "string" ? document.createTextNode(c) : c);
  return node;
}

function parseRoute(): Route {
  const hash = window.location.hash || "";
  const m = /^#\/torrent\/(\d+)$/.exec(hash);
  if (m) return { view: "detail", id: Number(m[1]) };
  if (hash === "#/settings") return { view: "settings" };
  return { view: "list" };
}

function setHashList(): void {
  window.location.hash = "";
}

function setHashDetail(id: number): void {
  window.location.hash = `#/torrent/${id}`;
}

function setHashSettings(): void {
  window.location.hash = "#/settings";
}

function navLink(label: string, onClick: () => void): HTMLElement {
  const a = el("a", { href: "#", className: "back-link" }, [label]);
  a.addEventListener("click", (ev) => {
    ev.preventDefault();
    onClick();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  return a;
}

function fmtPercent(v: number | null | undefined): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "0%";
  return `${Math.max(0, Math.min(100, v * 100)).toFixed(1)}%`;
}

function fmtRate(v: number | null | undefined): string {
  if (!v || v <= 0) return "—";
  const kb = v / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB/s`;
  return `${(kb / 1024).toFixed(1)} MB/s`;
}

function fmtBytes(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v < 0) return "—";
  if (v === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"] as const;
  let n = v;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  if (i === 0) return `${n} ${units[i]}`;
  const digits = i === 1 ? 0 : 1;
  return `${n.toFixed(digits)} ${units[i]}`;
}

function fmtRatio(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v < 0) return "—";
  if (v >= 1000) return "∞";
  return v.toFixed(2);
}

function fmtEta(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0 || !Number.isFinite(seconds)) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}д ${h}ч`;
  if (h > 0) return `${h}ч ${m}м`;
  if (m > 0) return `${m}м ${s}с`;
  return `${s}с`;
}

function fmtLimit(v: number | null | undefined): string {
  if (v == null || v <= 0) return "∞";
  return fmtRate(v);
}

const FILE_PRIORITY_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: "Не качать" },
  { value: 1, label: "Низкий" },
  { value: 4, label: "Обычный" },
  { value: 7, label: "Высокий" },
];

async function postAction(path: string): Promise<void> {
  await fetchJson(path, { method: "POST" });
}

function filterAndSortItems(items: TorrentOut[]): TorrentOut[] {
  let out = items.slice();
  const q = listSearch.trim().toLowerCase();
  if (q) {
    out = out.filter(
      (t) =>
        (t.display_name || "").toLowerCase().includes(q) ||
        (t.label || "").toLowerCase().includes(q) ||
        (t.info_hash || "").toLowerCase().includes(q),
    );
  }
  if (listStatusFilter) {
    out = out.filter((t) => effectiveStatus(t) === listStatusFilter);
  }
  if (listLabelFilter) {
    out = out.filter((t) => (t.label || "") === listLabelFilter);
  }
  out.sort((a, b) => {
    if (listSort === "name") return (a.display_name || "").localeCompare(b.display_name || "", "ru");
    if (listSort === "progress") return (b.runtime?.progress ?? 0) - (a.runtime?.progress ?? 0);
    if (listSort === "up") return (b.runtime?.upload_rate ?? 0) - (a.runtime?.upload_rate ?? 0);
    return b.id - a.id;
  });
  return out;
}

async function loadSessionStats(): Promise<SessionStats | null> {
  try {
    return await fetchJson<SessionStats>("/session/stats");
  } catch {
    return null;
  }
}

function mountSessionBar(stats: SessionStats | null): HTMLElement {
  const bar = el("div", { className: "session-bar" });
  if (!stats) {
    bar.append(el("span", { className: "session-bar__muted" }, ["Статистика недоступна"]));
    return bar;
  }
  bar.append(
    el("span", {}, [`Раздач: ${stats.torrents} (${stats.torrents_active} актив.)`]),
    el("span", {}, [`↓ ${fmtRate(stats.download_rate)}`]),
    el("span", {}, [`↑ ${fmtRate(stats.upload_rate)}`]),
    el("span", {}, [`Всего отдано: ${fmtBytes(stats.total_uploaded)}`]),
  );
  return bar;
}

function mountGlobalLimitsPanel(): HTMLElement {
  const panel = el("details", { className: "panel panel--compact" });
  const body = el("div", { className: "panel__body" });
  const dlInput = el("input", { type: "number", min: "0", placeholder: "∞" }) as HTMLInputElement;
  const ulInput = el("input", { type: "number", min: "0", placeholder: "∞" }) as HTMLInputElement;
  const applyBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, [
    "Применить на все движки",
  ]);
  applyBtn.addEventListener("click", async () => {
    applyBtn.disabled = true;
    const parse = (s: string) => {
      const n = Number(s.trim());
      return Number.isFinite(n) && n > 0 ? Math.round(n * 1024) : 0;
    };
    try {
      await fetchJson("/session/limits", {
        method: "POST",
        body: JSON.stringify({ download_limit: parse(dlInput.value), upload_limit: parse(ulInput.value) }),
      });
      showToast("Глобальные лимиты применены");
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      applyBtn.disabled = false;
    }
  });
  body.append(
    el("p", { className: "field__hint" }, ["Лимиты сессии libtorrent (0 = без ограничения)"]),
    el("div", { className: "limits-form" }, [
      el("label", { className: "limits-form__field" }, ["↓ КБ/с", dlInput]),
      el("label", { className: "limits-form__field" }, ["↑ КБ/с", ulInput]),
      applyBtn,
    ]),
  );
  panel.append(el("summary", {}, ["Глобальные лимиты"]), body);
  return panel;
}

function effectiveStatus(t: TorrentOut | TorrentDetailOut): string {
  const rs = (t.runtime?.runtime_status || "").toLowerCase();
  const lt = (t.runtime?.lt_state || "").toLowerCase();
  const progress = t.runtime?.progress;
  if (rs === "paused" || t.status === "paused") return "paused";
  if (lt === "seeding" || lt === "finished") return "seeding";
  if (progress != null && progress >= 0.999 && lt !== "downloading" && lt !== "downloading_metadata") {
    return "seeding";
  }
  if (lt === "downloading_metadata") return "downloading";
  return t.status;
}

function statusLabel(status: string, ltState?: string | null): string {
  if (ltState === "downloading_metadata") return "Метаданные";
  const map: Record<string, string> = {
    downloading: "Загрузка",
    seeding: "Раздача",
    paused: "Пауза",
    queued: "В очереди",
    error: "Ошибка",
  };
  return map[status] ?? status;
}

function displayStatusLabel(t: TorrentOut | TorrentDetailOut): string {
  const st = effectiveStatus(t);
  if (t.runtime?.lt_state === "downloading_metadata") return "Метаданные";
  return statusLabel(st, t.runtime?.lt_state);
}

function badgeClass(status: string): string {
  if (status === "seeding") return "badge badge--seeding";
  if (status === "paused") return "badge badge--paused";
  if (status === "queued") return "badge badge--queued";
  return "badge badge--downloading";
}

function isActivelyDownloading(t: TorrentOut): boolean {
  const p = t.runtime?.progress ?? 0;
  if (p >= 0.999 && effectiveStatus(t) === "seeding") return false;
  const st = t.runtime?.lt_state ?? t.status;
  return (
    (effectiveStatus(t) === "downloading" || st === "downloading" || st === "downloading_metadata") &&
    p < 0.999
  );
}

function pickListPollMs(items: TorrentOut[]): number {
  if (document.hidden) return 0;
  if (items.length === 0) return POLL_MS.empty;
  if (items.some(isActivelyDownloading)) return POLL_MS.active;
  return POLL_MS.idle;
}

function pickDetailPollMs(data: TorrentDetailOut): number {
  if (document.hidden) return 0;
  if (isActivelyDownloading(data)) return POLL_MS.active;
  return POLL_MS.idle;
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function buildDetailsSpoiler(summary: string, inner: HTMLElement): HTMLDetailsElement {
  const d = el("details", { className: "details-block" }) as HTMLDetailsElement;
  d.append(el("summary", {}, [summary]), inner);
  return d;
}

function buildPeersSpoiler(peers: TorrentPeerOut[], torrentId: number): HTMLDetailsElement {
  const inner = el("div", { className: "details-block__content" });
  if (peers.length === 0) {
    inner.append(el("p", { className: "details-block__empty" }, ["Нет подключённых пиров"]));
    const empty = buildDetailsSpoiler("Пиры (0)", inner);
    applyDetailSpoilerState(empty, torrentId, "peers");
    return empty;
  }
  const table = el("table", { className: "peer-table" });
  const headRow = el("tr");
  for (const label of ["Адрес", "Клиент", "%", "↓", "↑", "Флаги", "Источник"]) {
    headRow.append(el("th", {}, [label]));
  }
  const body = el("tbody");
  for (const p of peers) {
    const row = el("tr");
    const pct =
      p.progress === null || p.progress === undefined ? "—" : `${Math.round(p.progress * 1000) / 10}%`;
    row.append(
      el("td", { className: "peer-table__mono" }, [p.endpoint || "—"]),
      el("td", {}, [p.client || "—"]),
      el("td", { className: "peer-table__num" }, [pct]),
      el("td", { className: "peer-table__num" }, [fmtRate(p.download_rate)]),
      el("td", { className: "peer-table__num" }, [fmtRate(p.upload_rate)]),
      el("td", { className: "peer-table__flags" }, [p.flags || "—"]),
      el("td", {}, [p.source || "—"]),
    );
    body.append(row);
  }
  table.append(el("thead", {}, [headRow]), body);
  inner.append(table);
  const d = buildDetailsSpoiler(`Пиры (${peers.length})`, inner);
  applyDetailSpoilerState(d, torrentId, "peers");
  return d;
}

function buildFilesSpoiler(files: TorrentFileOut[], torrentId: number, onChange: () => void): HTMLDetailsElement {
  const inner = el("div", { className: "details-block__content" });
  if (files.length === 0) {
    inner.append(el("p", { className: "details-block__empty" }, ["Список файлов недоступен (нет метаданных)"]));
    const empty = buildDetailsSpoiler("Файлы (0)", inner);
    applyDetailSpoilerState(empty, torrentId, "files");
    return empty;
  }
  const table = el("table", { className: "peer-table file-table" });
  const headRow = el("tr");
  for (const label of ["Файл", "Размер", "%", "Приоритет"]) headRow.append(el("th", {}, [label]));
  const body = el("tbody");
  for (const f of files) {
    const row = el("tr");
    const pct = `${Math.round((f.progress ?? 0) * 1000) / 10}%`;
    const select = el("select", { className: "file-prio" }) as HTMLSelectElement;
    for (const opt of FILE_PRIORITY_OPTIONS) {
      const o = el("option", { value: String(opt.value) }, [opt.label]) as HTMLOptionElement;
      if (opt.value === f.priority || (opt.value === 4 && f.priority > 0 && f.priority !== 1 && f.priority !== 7))
        o.selected = true;
      select.append(o);
    }
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await fetchJson(`/torrents/${torrentId}/files/priorities`, {
          method: "POST",
          body: JSON.stringify({ priorities: { [f.index]: Number(select.value) } }),
        });
        showToast("Приоритет обновлён");
        onChange();
      } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), true);
        select.disabled = false;
      }
    });
    row.append(
      el("td", { className: "file-table__name" }, [f.path]),
      el("td", { className: "peer-table__num" }, [fmtBytes(f.size)]),
      el("td", { className: "peer-table__num" }, [pct]),
      el("td", {}, [select]),
    );
    body.append(row);
  }
  table.append(el("thead", {}, [headRow]), body);
  inner.append(table);
  const d = buildDetailsSpoiler(`Файлы (${files.length})`, inner);
  applyDetailSpoilerState(d, torrentId, "files");
  return d;
}

function buildTrackersSpoiler(
  trackers: TorrentTrackerOut[],
  torrentId: number,
  onReannounce: () => void,
): HTMLDetailsElement {
  const inner = el("div", { className: "details-block__content" });
  const announceRow = el("div", { className: "btn-row" });
  const annBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Переанонсировать"]);
  annBtn.addEventListener("click", async () => {
    annBtn.disabled = true;
    try {
      await postAction(`/torrents/${torrentId}/reannounce`);
      showToast("Переанонс отправлен");
      onReannounce();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      annBtn.disabled = false;
    }
  });
  announceRow.append(annBtn);

  const addRow = el("div", { className: "tracker-add-row" });
  const urlInput = el("input", {
    type: "url",
    placeholder: "https://tracker.example/announce",
  }) as HTMLInputElement;
  const addBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Добавить"]);
  addBtn.addEventListener("click", async () => {
    const url = urlInput.value.trim();
    if (!url) return;
    addBtn.disabled = true;
    try {
      await fetchJson(`/torrents/${torrentId}/trackers`, {
        method: "POST",
        body: JSON.stringify({ url }),
      });
      urlInput.value = "";
      showToast("Трекер добавлен");
      onReannounce();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      addBtn.disabled = false;
    }
  });
  addRow.append(urlInput, addBtn);
  inner.append(announceRow, addRow);

  if (trackers.length === 0) {
    inner.append(el("p", { className: "details-block__empty" }, ["Нет трекеров"]));
  } else {
    const table = el("table", { className: "peer-table" });
    const headRow = el("tr");
    for (const label of ["Трекер", "Сообщение", "Пиры", "✓", ""]) headRow.append(el("th", {}, [label]));
    const body = el("tbody");
    for (const t of trackers) {
      const row = el("tr");
      const delBtn = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["×"]);
      delBtn.addEventListener("click", async () => {
        delBtn.disabled = true;
        try {
          await fetchJson(`/torrents/${torrentId}/trackers?url=${encodeURIComponent(t.url)}`, {
            method: "DELETE",
          });
          showToast("Трекер удалён");
          onReannounce();
        } catch (e) {
          showToast(e instanceof Error ? e.message : String(e), true);
          delBtn.disabled = false;
        }
      });
      row.append(
        el("td", { className: "peer-table__mono" }, [t.url]),
        el("td", {}, [t.message || "—"]),
        el("td", { className: "peer-table__num" }, [String(t.num_peers)]),
        el("td", { className: "peer-table__num" }, [t.verified ? "✓" : "—"]),
        el("td", {}, [delBtn]),
      );
      body.append(row);
    }
    table.append(el("thead", {}, [headRow]), body);
    inner.append(table);
  }
  const d = buildDetailsSpoiler(`Трекеры (${trackers.length})`, inner);
  applyDetailSpoilerState(d, torrentId, "trackers");
  return d;
}

function buildLimitsForm(data: TorrentDetailOut, onApplied: () => void): HTMLElement {
  const wrap = el("div", { className: "limits-form" });
  const toKb = (v: number | null | undefined) => (v && v > 0 ? String(Math.round(v / 1024)) : "");
  const dlInput = el("input", {
    type: "number",
    min: "0",
    placeholder: "∞",
    value: toKb(data.runtime?.download_limit),
  }) as HTMLInputElement;
  const ulInput = el("input", {
    type: "number",
    min: "0",
    placeholder: "∞",
    value: toKb(data.runtime?.upload_limit),
  }) as HTMLInputElement;
  const applyBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Применить лимиты"]);
  applyBtn.addEventListener("click", async () => {
    applyBtn.disabled = true;
    const parse = (s: string): number => {
      const n = Number(s.trim());
      return Number.isFinite(n) && n > 0 ? Math.round(n * 1024) : 0;
    };
    try {
      await fetchJson(`/torrents/${data.id}/limits`, {
        method: "POST",
        body: JSON.stringify({ download_limit: parse(dlInput.value), upload_limit: parse(ulInput.value) }),
      });
      showToast("Лимиты применены");
      onApplied();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      applyBtn.disabled = false;
    }
  });
  wrap.append(
    el("label", { className: "limits-form__field" }, ["↓ КБ/с", dlInput]),
    el("label", { className: "limits-form__field" }, ["↑ КБ/с", ulInput]),
    applyBtn,
  );
  return wrap;
}

function showDeleteTorrentDialog(torrent: { id: number; display_name?: string }): Promise<DeleteTorrentChoice> {
  return new Promise((resolve) => {
    const overlay = el("div", { className: "modal-overlay" });
    const title = torrent.display_name?.trim() || `торрент #${torrent.id}`;
    const dialog = el("div", {
      className: "modal-dialog",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": "delete-dialog-title",
    });
    dialog.append(
      el("h2", { id: "delete-dialog-title", className: "modal-title" }, ["Удалить торрент?"]),
      el("p", { className: "modal-text" }, [`«${title}»`]),
      (() => {
        const actions = el("div", { className: "modal-actions" });
        const cancelBtn = el("button", { type: "button", className: "btn btn--ghost" }, ["Отмена"]);
        const keepBtn = el("button", { type: "button", className: "btn" }, ["Только из списка"]);
        const allBtn = el("button", { type: "button", className: "btn btn--danger" }, [
          "И файлы с диска",
        ]);
        const finish = (c: DeleteTorrentChoice) => {
          overlay.remove();
          document.removeEventListener("keydown", onKey);
          resolve(c);
        };
        cancelBtn.addEventListener("click", () => finish("cancel"));
        keepBtn.addEventListener("click", () => finish("torrent_only"));
        allBtn.addEventListener("click", () => finish("torrent_and_files"));
        overlay.addEventListener("click", (ev) => {
          if (ev.target === overlay) finish("cancel");
        });
        const onKey = (ev: KeyboardEvent) => {
          if (ev.key === "Escape") finish("cancel");
        };
        document.addEventListener("keydown", onKey);
        actions.append(cancelBtn, keepBtn, allBtn);
        keepBtn.focus();
        return actions;
      })(),
    );
    overlay.append(dialog);
    document.body.append(overlay);
  });
}

async function deleteTorrentWithDialog(
  torrent: { id: number; display_name?: string },
  onDone: () => void | Promise<void>,
): Promise<void> {
  const choice = await showDeleteTorrentDialog(torrent);
  if (choice === "cancel") return;
  try {
    await fetchDelete(`/torrents/${torrent.id}`, choice === "torrent_and_files");
    showToast("Торрент удалён");
    await onDone();
  } catch (e) {
    showToast(e instanceof Error ? e.message : String(e), true);
  }
}

function renderTorrentCard(
  t: TorrentOut,
  onChange: () => void,
  onSelectToggle: (id: number, checked: boolean) => void,
): HTMLElement {
  const progress = t.runtime?.progress ?? 0;
  const pct = Math.round(progress * 1000) / 10;
  const card = el("li", { className: "torrent-card" });
  const checkbox = el("input", { type: "checkbox", className: "torrent-card__check" }) as HTMLInputElement;
  checkbox.checked = selectedIds.has(t.id);
  checkbox.addEventListener("change", () => onSelectToggle(t.id, checkbox.checked));
  const title = el(
    "h3",
    { className: "torrent-card__title" },
    [
      (() => {
        const fullName = t.display_name || `Торрент #${t.id}`;
        const shownName = fullName.replace(/\.torrent$/i, "") || fullName;
        const a = el("a", { href: `#/torrent/${t.id}`, title: fullName }, [shownName]);
        a.addEventListener("click", (ev) => {
          ev.preventDefault();
          setHashDetail(t.id);
          window.dispatchEvent(new HashChangeEvent("hashchange"));
        });
        return a;
      })(),
    ],
  );
  const st = effectiveStatus(t);
  const badge = el("span", { className: badgeClass(st) }, [displayStatusLabel(t)]);
  const labelBadge =
    t.label && t.label.trim()
      ? el("span", { className: "badge badge--label" }, [t.label])
      : null;
  const bar = el("div", { className: "progress" });
  const barInner = el("div", {
    className: `progress__bar${pct >= 100 ? " progress__bar--complete" : ""}`,
    style: `width:${pct}%`,
    role: "progressbar",
    "aria-valuenow": String(pct),
    "aria-valuemin": "0",
    "aria-valuemax": "100",
  });
  bar.append(barInner);
  const stats = el("div", { className: "torrent-card__stats" });
  const sizeStr = t.runtime?.size ? fmtBytes(t.runtime.size) : null;
  stats.append(
    document.createTextNode(`${fmtPercent(t.runtime?.progress)}${sizeStr ? ` из ${sizeStr}` : ""} · `),
    el("strong", {}, [`↓ ${fmtRate(t.runtime?.download_rate)}`]),
    document.createTextNode(" · "),
    el("strong", {}, [`↑ ${fmtRate(t.runtime?.upload_rate)}`]),
    document.createTextNode(
      ` · R ${fmtRatio(t.runtime?.ratio)} · ${t.runtime?.num_seeds ?? 0}↑/${t.runtime?.peers ?? 0} пир.`,
    ),
  );
  const actions = el("div", { className: "btn-row" });
  const pauseBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Пауза"]);
  const resumeBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Старт"]);
  const delBtn = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["Удалить"]);
  pauseBtn.addEventListener("click", async () => {
    try {
      await fetchJson(`/torrents/${t.id}/pause`, { method: "POST" });
      await onChange();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  });
  resumeBtn.addEventListener("click", async () => {
    try {
      await fetchJson(`/torrents/${t.id}/resume`, { method: "POST" });
      await onChange();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  });
  delBtn.addEventListener("click", () => {
    void deleteTorrentWithDialog({ id: t.id, display_name: t.display_name }, onChange);
  });
  if (t.status === "paused") pauseBtn.disabled = true;
  else resumeBtn.disabled = true;
  actions.append(pauseBtn, resumeBtn, delBtn);
  const topRight = el("div", { className: "torrent-card__badges" }, [badge]);
  if (labelBadge) topRight.append(labelBadge);
  card.append(
    el("div", { className: "torrent-card__top" }, [checkbox, title, topRight]),
    bar,
    stats,
    actions,
  );
  return card;
}

function updateLiveMeta(metaEl: HTMLElement, items: TorrentOut[]): void {
  const active = items.some(isActivelyDownloading);
  metaEl.replaceChildren(
    el("span", { className: active ? "live-dot" : "live-dot live-dot--paused" }),
    document.createTextNode(`Обновлено ${formatTime(new Date())}`),
  );
}

function paintTorrentList(refs: ListHostRefs, items: TorrentOut[]): void {
  const { listEl, countEl, metaEl } = refs;
  const filtered = filterAndSortItems(items);
  const total = items.length;
  const shown = filtered.length;
  countEl.textContent =
    shown === total
      ? `${total} ${total === 1 ? "торрент" : total < 5 ? "торрента" : "торрентов"}`
      : `${shown} из ${total}`;
  updateLiveMeta(metaEl, items);
  listEl.replaceChildren();
  if (shown === 0) {
    listEl.append(
      el("div", { className: "empty-state" }, [
        el("p", {}, [total === 0 ? "Пока пусто" : "Ничего не найдено"]),
        el("p", {}, [total === 0 ? "Добавьте magnet, URL или .torrent ниже" : "Измените фильтр"]),
      ]),
    );
    return;
  }
  // Чистим выделение от исчезнувших раздач, чтобы счётчик и массовые действия были точны.
  const presentIds = new Set(items.map((t) => t.id));
  let pruned = false;
  for (const id of [...selectedIds]) {
    if (!presentIds.has(id)) {
      selectedIds.delete(id);
      pruned = true;
    }
  }
  if (pruned) selectionChanged?.();

  const ul = el("ul", { className: "torrent-list" });
  const refresh = () =>
    void loadTorrents(refs.listEl, refs.countEl, refs.metaEl, {
      silent: true,
      scheduleNext: refs.scheduleNext,
    });
  const onSelectToggle = (id: number, checked: boolean) => {
    if (checked) selectedIds.add(id);
    else selectedIds.delete(id);
    selectionChanged?.();
  };
  for (const t of filtered) ul.append(renderTorrentCard(t, refresh, onSelectToggle));
  listEl.append(ul);
}

function showTorrentInList(refs: ListHostRefs, torrent: TorrentOut): void {
  paintTorrentList(refs, upsertTorrentInList(torrent));
}

async function loadTorrents(
  listEl: HTMLElement,
  countEl: HTMLElement,
  metaEl: HTMLElement,
  opts: {
    silent?: boolean;
    scheduleNext?: (items: TorrentOut[], pollOpts?: { fast?: boolean }) => void;
    fastPoll?: boolean;
  } = {},
): Promise<void> {
  stopListPoll();
  listAbort?.abort();
  const gen = ++listLoadGeneration;
  listAbort = new AbortController();
  const signal = listAbort.signal;
  if (!opts.silent) listEl.classList.add("is-loading");

  try {
    const items = await fetchJson<TorrentOut[]>("/torrents", { signal });
    if (gen !== listLoadGeneration) return;
    lastListItems = items;
    paintTorrentList({ listEl, countEl, metaEl, scheduleNext: opts.scheduleNext ?? (() => {}) }, items);
    opts.scheduleNext?.(items, opts.fastPoll ? { fast: true } : undefined);
  } catch (e) {
    if (gen !== listLoadGeneration) return;
    showToast(e instanceof Error ? e.message : String(e), true);
  } finally {
    if (gen === listLoadGeneration) listEl.classList.remove("is-loading");
  }
}

function scheduleListPoll(
  listEl: HTMLElement,
  countEl: HTMLElement,
  metaEl: HTMLElement,
  items: TorrentOut[],
  pollOpts?: { fast?: boolean },
): void {
  stopListPoll();
  const ms = pollOpts?.fast ? POLL_MS.active : pickListPollMs(items);
  if (ms <= 0 || parseRoute().view !== "list") return;
  listPollTimer = setTimeout(() => {
    void loadTorrents(listEl, countEl, metaEl, {
      silent: true,
      scheduleNext: (next, nextOpts) => scheduleListPoll(listEl, countEl, metaEl, next, nextOpts),
    });
  }, ms);
}

// Push-обновления через SSE. При успехе вытесняют поллинг; при ошибке — откат на поллинг.
function startListStream(refs: ListHostRefs, sessionBarHost: HTMLElement, onFallback: () => void): void {
  stopListStream();
  let url = `${API}/stream?interval=3`;
  try {
    const key = localStorage.getItem("seedingApiKey");
    if (key) url += `&api_key=${encodeURIComponent(key)}`;
  } catch {
    /* ignore */
  }
  let es: EventSource;
  try {
    es = new EventSource(url);
  } catch {
    onFallback();
    return;
  }
  listStream = es;
  es.addEventListener("snapshot", (ev) => {
    if (listStream !== es || parseRoute().view !== "list") return;
    try {
      const data = JSON.parse((ev as MessageEvent).data) as { torrents: TorrentOut[]; stats: SessionStats };
      stopListPoll();
      lastListItems = data.torrents;
      paintTorrentList(refs, data.torrents);
      sessionBarHost.replaceChildren(mountSessionBar(data.stats));
    } catch {
      /* ignore malformed frame */
    }
  });
  es.onerror = () => {
    // EventSource сам пытается переподключаться; не блокируем UI — откатываемся на поллинг.
    if (listStream === es) {
      stopListStream();
      onFallback();
    }
  };
}

function mountAddPanel(savePathDefault: string, onAdded: (created?: TorrentOut) => void): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["Добавить торрент"]));
  const body = el("div", { className: "panel__body" });

  const tabs = el("div", { className: "tabs" });
  const tabMagnet = el("button", { type: "button", className: "tab tab--active", "data-tab": "magnet" }, [
    "Magnet",
  ]);
  const tabUrl = el("button", { type: "button", className: "tab", "data-tab": "url" }, ["URL"]);
  const tabFile = el("button", { type: "button", className: "tab", "data-tab": "file" }, ["Файл"]);
  tabs.append(tabMagnet, tabUrl, tabFile);

  const magnetPanel = el("div", { className: "tab-panel", "data-panel": "magnet" });
  const urlPanel = el("div", { className: "tab-panel", "data-panel": "url", hidden: "" });
  const filePanel = el("div", { className: "tab-panel", "data-panel": "file", hidden: "" });

  const magnetInput = el("input", {
    type: "text",
    placeholder: "magnet:?xt=urn:btih:…",
  }) as HTMLInputElement;
  const urlInput = el("input", {
    type: "url",
    placeholder: "https://example.com/file.torrent",
  }) as HTMLInputElement;
  const engineSelect = el("select", { className: "select" }) as HTMLSelectElement;
  engineSelect.append(el("option", { value: "" }, ["Загрузка движков…"]));
  const customPathInput = el("input", {
    type: "text",
    placeholder: `Напр. ${savePathDefault || "/data/b1"}/movies`,
    value: "",
  }) as HTMLInputElement;
  const labelInput = el("input", { type: "text", placeholder: "Метка (необязательно)" }) as HTMLInputElement;
  const nameMagnet = el("input", { type: "text", placeholder: "Название (необязательно)" }) as HTMLInputElement;
  const nameUrl = el("input", { type: "text", placeholder: "Название (необязательно)" }) as HTMLInputElement;
  const torrentFile = el("input", { type: "file", accept: ".torrent", multiple: "" }) as HTMLInputElement;
  const nameFile = el("input", { type: "text", placeholder: "Название (необязательно)" }) as HTMLInputElement;

  const switchTab = (name: "magnet" | "url" | "file") => {
    tabMagnet.classList.toggle("tab--active", name === "magnet");
    tabUrl.classList.toggle("tab--active", name === "url");
    tabFile.classList.toggle("tab--active", name === "file");
    magnetPanel.hidden = name !== "magnet";
    urlPanel.hidden = name !== "url";
    filePanel.hidden = name !== "file";
  };
  tabMagnet.addEventListener("click", () => switchTab("magnet"));
  tabUrl.addEventListener("click", () => switchTab("url"));
  tabFile.addEventListener("click", () => switchTab("file"));

  magnetPanel.append(
    field("Magnet-ссылка", magnetInput),
    field("Название", nameMagnet),
    el("div", { className: "btn-row" }, [
      el("button", { type: "button", className: "btn btn--primary", id: "btn-add-magnet" }, ["Добавить"]),
    ]),
  );

  urlPanel.append(
    field("URL .torrent", urlInput),
    field("Название", nameUrl),
    el("div", { className: "btn-row" }, [
      el("button", { type: "button", className: "btn btn--primary", id: "btn-add-url" }, ["Загрузить по URL"]),
    ]),
  );

  filePanel.append(
    field("Файлы .torrent", torrentFile, "Можно выбрать сразу несколько файлов"),
    field("Название", nameFile, "Используется только при загрузке одного файла"),
    el("div", { className: "btn-row" }, [
      el("button", { type: "button", className: "btn btn--primary", id: "btn-add-file" }, ["Загрузить"]),
    ]),
  );

  const advanced = el("details", { className: "advanced" });
  advanced.append(
    el("summary", {}, ["Дополнительно: свой путь"]),
    field(
      "Папка на сервере",
      customPathInput,
      "Если задано — переопределяет выбор движка. Обычно /data/b1 для движка b1.",
    ),
  );

  body.append(
    field("Движок", engineSelect, "Контент сохраняется в хранилище выбранного движка"),
    advanced,
    field("Метка", labelInput),
    tabs,
    magnetPanel,
    urlPanel,
    filePanel,
  );

  void (async () => {
    try {
      const engines = await fetchJson<EngineOut[]>("/engines");
      engineSelect.replaceChildren();
      if (engines.length === 0) {
        engineSelect.append(el("option", { value: "" }, ["Нет движков"]));
        return;
      }
      let firstOnline = "";
      for (const e of engines) {
        const free = e.disk_free != null ? `своб. ${fmtBytes(e.disk_free)}` : "место неизв.";
        const total = e.disk_total != null ? ` из ${fmtBytes(e.disk_total)}` : "";
        const off = e.online === false ? " — офлайн" : "";
        const opt = el("option", { value: e.id }, [`${e.id} · ${free}${total}${off}`]) as HTMLOptionElement;
        if (e.online === false) opt.disabled = true;
        else if (!firstOnline) firstOnline = e.id;
        engineSelect.append(opt);
      }
      if (firstOnline) engineSelect.value = firstOnline;
    } catch (e) {
      engineSelect.replaceChildren(el("option", { value: "" }, ["Ошибка загрузки движков"]));
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  })();

  // Куда добавлять: свой путь (если задан) приоритетнее выбора движка.
  const targetJson = (): { engine_id?: string; save_path?: string } | null => {
    const custom = customPathInput.value.trim();
    if (custom) return { save_path: custom };
    const eid = engineSelect.value;
    if (!eid) {
      showToast("Выберите движок или укажите свой путь", true);
      return null;
    }
    return { engine_id: eid };
  };
  const applyTargetToForm = (form: FormData): boolean => {
    const t = targetJson();
    if (!t) return false;
    if (t.engine_id) form.set("engine_id", t.engine_id);
    if (t.save_path) form.set("save_path", t.save_path);
    return true;
  };

  magnetPanel.querySelector("#btn-add-magnet")?.addEventListener("click", async () => {
    const magnet_uri = magnetInput.value.trim();
    if (!magnet_uri) {
      showToast("Укажите magnet", true);
      return;
    }
    const target = targetJson();
    if (!target) return;
    try {
      const created = await fetchJson<TorrentOut>("/torrents", {
        method: "POST",
        body: JSON.stringify({
          magnet_uri,
          ...target,
          display_name: nameMagnet.value.trim(),
          label: labelInput.value.trim(),
        }),
      });
      magnetInput.value = "";
      nameMagnet.value = "";
      showToast("Торрент добавлен");
      onAdded(created);
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  });

  urlPanel.querySelector("#btn-add-url")?.addEventListener("click", async () => {
    const url = urlInput.value.trim();
    if (!url) {
      showToast("Укажите URL", true);
      return;
    }
    const target = targetJson();
    if (!target) return;
    try {
      const created = await fetchJson<TorrentOut>("/torrents/url", {
        method: "POST",
        body: JSON.stringify({
          url,
          ...target,
          display_name: nameUrl.value.trim(),
          label: labelInput.value.trim(),
        }),
      });
      urlInput.value = "";
      nameUrl.value = "";
      showToast("Торрент загружен по URL");
      onAdded(created);
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  });

  filePanel.querySelector("#btn-add-file")?.addEventListener("click", async () => {
    const files = torrentFile.files ? Array.from(torrentFile.files) : [];
    if (files.length === 0) {
      showToast("Выберите файл(ы)", true);
      return;
    }
    try {
      if (files.length === 1) {
        const body = new FormData();
        body.set("torrent_file", files[0], files[0].name);
        if (!applyTargetToForm(body)) return;
        body.set("display_name", nameFile.value.trim());
        body.set("label", labelInput.value.trim());
        const res = await fetch(`${API}/torrents/upload`, { method: "POST", headers: apiHeaders(false), body });
        await throwIfNotOk(res);
        const created = (await res.json()) as TorrentOut;
        torrentFile.value = "";
        nameFile.value = "";
        showToast("Торрент загружен");
        onAdded(created);
        return;
      }

      const body = new FormData();
      for (const f of files) body.append("torrent_files", f, f.name);
      if (!applyTargetToForm(body)) return;
      body.set("label", labelInput.value.trim());
      const res = await fetch(`${API}/torrents/upload-batch`, { method: "POST", headers: apiHeaders(false), body });
      await throwIfNotOk(res);
      const result = (await res.json()) as BatchUploadResult;
      torrentFile.value = "";
      if (result.failed === 0) {
        showToast(`Добавлено торрентов: ${result.ok}`);
      } else {
        const failed = result.items.filter((i) => !i.ok).map((i) => i.filename).join(", ");
        showToast(`Добавлено ${result.ok}, ошибок ${result.failed}: ${failed}`, true);
      }
      if (result.ok > 0) onAdded();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  });

  panel.append(body);
  return panel;
}

function showAddTorrentDialog(savePathDefault: string, onAdded: (created?: TorrentOut) => void): void {
  const overlay = el("div", { className: "modal-overlay" });
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") close();
  };

  const panel = mountAddPanel(savePathDefault, (created) => {
    close();
    onAdded(created);
  });
  panel.classList.add("modal-panel");

  const closeBtn = el(
    "button",
    { type: "button", className: "btn btn--ghost btn--sm modal-close", "aria-label": "Закрыть" },
    ["✕"],
  );
  closeBtn.addEventListener("click", close);
  const head = panel.querySelector(".panel__head");
  if (head) {
    head.classList.add("panel__head--with-action");
    head.append(closeBtn);
  }

  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  document.addEventListener("keydown", onKey);

  overlay.append(panel);
  document.body.append(overlay);
}

function field(label: string, input: HTMLElement, hint?: string): HTMLElement {
  const f = el("div", { className: "field" });
  f.append(el("label", {}, [label, input]));
  if (hint) f.append(el("span", { className: "field__hint" }, [hint]));
  return f;
}

function mountListShell(root: HTMLElement): void {
  const metaEl = el("div", { className: "app-header__meta" });
  const listHost = el("div", { id: "torrent-list-host" });
  const countEl = el("span", { className: "list-toolbar__count" });
  const sessionBarHost = el("div", { id: "session-bar-host" });

  const listRefs: ListHostRefs = {
    listEl: listHost,
    countEl,
    metaEl,
    scheduleNext: (items, pollOpts) => scheduleListPoll(listHost, countEl, metaEl, items, pollOpts),
  };

  const refresh = (opts?: { afterAdd?: boolean }) =>
    void loadTorrents(listHost, countEl, metaEl, {
      silent: lastListItems.length > 0 && !opts?.afterAdd,
      fastPoll: opts?.afterAdd,
      scheduleNext: listRefs.scheduleNext,
    });

  const repaint = () => paintTorrentList(listRefs, lastListItems);

  const onAdded = (created?: TorrentOut) => {
    if (created) showTorrentInList(listRefs, created);
    void refresh({ afterAdd: true });
  };

  const searchInput = el("input", {
    type: "search",
    placeholder: "Поиск по названию, метке, hash…",
    className: "list-filter__search",
    value: listSearch,
  }) as HTMLInputElement;
  searchInput.addEventListener("input", () => {
    listSearch = searchInput.value;
    lsSet("ui.search", listSearch);
    repaint();
  });

  const statusSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["", "Все статусы"],
    ["seeding", "Раздача"],
    ["downloading", "Загрузка"],
    ["paused", "Пауза"],
  ]) {
    const o = el("option", { value: val }, [label]) as HTMLOptionElement;
    if (val === listStatusFilter) o.selected = true;
    statusSelect.append(o);
  }
  statusSelect.addEventListener("change", () => {
    listStatusFilter = statusSelect.value;
    lsSet("ui.status", listStatusFilter);
    repaint();
  });

  const labelSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  const labelSuggestions = el("datalist", { id: "label-suggestions" }) as HTMLDataListElement;
  const reloadLabels = async () => {
    labelSelect.replaceChildren(el("option", { value: "" }, ["Все метки"]));
    labelSuggestions.replaceChildren();
    try {
      const labels = await fetchJson<string[]>("/labels");
      for (const lb of labels) {
        const o = el("option", { value: lb }, [lb]) as HTMLOptionElement;
        if (lb === listLabelFilter) o.selected = true;
        labelSelect.append(o);
        labelSuggestions.append(el("option", { value: lb }));
      }
    } catch {
      /* ignore */
    }
  };
  labelSelect.addEventListener("change", () => {
    listLabelFilter = labelSelect.value;
    lsSet("ui.label", listLabelFilter);
    repaint();
  });

  const sortSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["added", "Сорт: новые"],
    ["name", "Сорт: имя"],
    ["progress", "Сорт: прогресс"],
    ["up", "Сорт: отдача"],
  ]) {
    const o = el("option", { value: val }, [label]) as HTMLOptionElement;
    if (val === listSort) o.selected = true;
    sortSelect.append(o);
  }
  sortSelect.addEventListener("change", () => {
    listSort = sortSelect.value as ListSort;
    lsSet("ui.sort", listSort);
    repaint();
  });

  const densitySelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["comfortable", "Вид: строки"],
    ["compact", "Вид: плитки"],
  ]) {
    const o = el("option", { value: val }, [label]) as HTMLOptionElement;
    if (val === listDensity) o.selected = true;
    densitySelect.append(o);
  }
  const applyDensity = () => {
    listHost.classList.toggle("torrent-list--compact", listDensity === "compact");
  };
  densitySelect.addEventListener("change", () => {
    listDensity = densitySelect.value === "compact" ? "compact" : "comfortable";
    lsSet("ui.density", listDensity);
    applyDensity();
  });

  const bulkPause = el("button", { type: "button", className: "btn btn--sm" }, ["⏸ Пауза"]);
  const bulkResume = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["▶ Старт"]);
  const bulkDel = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["🗑 Удалить"]);
  const runBulk = async (path: string) => {
    const ids = [...selectedIds];
    if (ids.length === 0) {
      showToast("Ничего не выбрано", true);
      return;
    }
    try {
      await fetchJson(path, { method: "POST", body: JSON.stringify({ ids }) });
      selectedIds.clear();
      syncBulkBar();
      showToast("Готово");
      void refresh();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  };
  bulkPause.addEventListener("click", () => void runBulk("/torrents/bulk/pause"));
  bulkResume.addEventListener("click", () => void runBulk("/torrents/bulk/resume"));

  const bulkLabelInput = el("input", {
    type: "text",
    className: "list-toolbar__label-input",
    placeholder: "Метка…",
    list: "label-suggestions",
  }) as HTMLInputElement;
  const bulkLabelBtn = el("button", { type: "button", className: "btn btn--sm" }, ["🏷 Метка"]);
  const applyBulkLabel = async () => {
    const ids = [...selectedIds];
    if (ids.length === 0) {
      showToast("Ничего не выбрано", true);
      return;
    }
    const label = bulkLabelInput.value.trim();
    try {
      await fetchJson("/torrents/bulk/label", { method: "POST", body: JSON.stringify({ ids, label }) });
      bulkLabelInput.value = "";
      selectedIds.clear();
      syncBulkBar();
      showToast(label ? `Метка «${label}» назначена` : "Метка снята");
      await reloadLabels();
      void refresh();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  };
  bulkLabelBtn.addEventListener("click", () => void applyBulkLabel());
  bulkLabelInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") void applyBulkLabel();
  });
  bulkDel.addEventListener("click", async () => {
    const ids = [...selectedIds];
    if (ids.length === 0) {
      showToast("Ничего не выбрано", true);
      return;
    }
    if (!window.confirm(`Удалить ${ids.length} торрент(ов) из списка?`)) return;
    try {
      await fetchJson("/torrents/bulk/delete", { method: "POST", body: JSON.stringify({ ids }) });
      selectedIds.clear();
      syncBulkBar();
      showToast("Удалено");
      void refresh();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  });

  const addTorrentBtn = el("button", { type: "button", className: "btn btn--primary btn--sm" }, ["+ Добавить торрент"]);
  addTorrentBtn.addEventListener("click", () => showAddTorrentDialog("/data/b1", onAdded));

  const settingsLink = el("button", { type: "button", className: "btn btn--ghost btn--sm" }, ["⚙ Настройки"]);
  settingsLink.addEventListener("click", () => {
    setHashSettings();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  const header = el("header", { className: "app-header" }, [
    el("div", {}, [el("h1", {}, ["Раздача"]), el("p", { className: "field__hint" }, ["Управление торрентами"])]),
    el("div", { className: "app-header__actions" }, [addTorrentBtn, settingsLink, metaEl]),
  ]);

  const resetFilters = el("button", { type: "button", className: "btn btn--ghost btn--sm" }, ["Сброс фильтров"]);
  resetFilters.addEventListener("click", () => {
    listSearch = "";
    listStatusFilter = "";
    listLabelFilter = "";
    listSort = "added";
    for (const k of ["ui.search", "ui.status", "ui.label", "ui.sort"]) lsSet(k, "");
    searchInput.value = "";
    statusSelect.value = "";
    labelSelect.value = "";
    sortSelect.value = "added";
    repaint();
  });

  const filters = el("div", { className: "list-filters" }, [
    searchInput,
    statusSelect,
    labelSelect,
    sortSelect,
    densitySelect,
    resetFilters,
  ]);
  applyDensity();

  // Обычная панель: только счётчик и обновление — без нагромождения кнопок.
  const refreshBtn = el("button", { type: "button", className: "btn btn--ghost btn--sm" }, ["Обновить"]);
  refreshBtn.addEventListener("click", () => void refresh());
  const toolbar = el("div", { className: "list-toolbar" }, [countEl, refreshBtn, labelSuggestions]);

  // Контекстная панель массовых действий: видна только когда что-то выбрано.
  const bulkCount = el("span", { className: "bulk-bar__count" });
  const clearSelBtn = el("button", { type: "button", className: "btn btn--ghost btn--sm" }, ["Снять"]);
  clearSelBtn.addEventListener("click", () => {
    selectedIds.clear();
    repaint();
    syncBulkBar();
  });
  const bulkBar = el("div", { className: "bulk-bar", hidden: "" }, [
    bulkCount,
    bulkResume,
    bulkPause,
    bulkLabelInput,
    bulkLabelBtn,
    bulkDel,
    el("span", { className: "bulk-bar__spacer" }),
    clearSelBtn,
  ]);
  function syncBulkBar(): void {
    const n = selectedIds.size;
    bulkBar.hidden = n === 0;
    bulkCount.textContent = `Выбрано: ${n}`;
  }
  selectionChanged = syncBulkBar;
  syncBulkBar();

  root.append(
    header,
    sessionBarHost,
    filters,
    toolbar,
    bulkBar,
    listHost,
  );

  void reloadLabels();
  void loadSessionStats().then((s) => {
    sessionBarHost.replaceChildren(mountSessionBar(s));
  });

  const startStream = () => startListStream(listRefs, sessionBarHost, () => void refresh());

  const onVisibility = () => {
    if (parseRoute().view !== "list") return;
    if (document.hidden) {
      stopListStream();
      stopListPoll();
    } else {
      void refresh();
      startStream();
    }
  };
  document.addEventListener("visibilitychange", onVisibility);

  void refresh();
  startStream();
}

async function loadDetail(
  id: number,
  container: HTMLElement,
  metaEl: HTMLElement,
  scheduleNext?: (data: TorrentDetailOut) => void,
): Promise<void> {
  detailAbort?.abort();
  detailAbort = new AbortController();
  const signal = detailAbort.signal;

  try {
    const data = await fetchJson<TorrentDetailOut>(`/torrents/${id}`, { signal });
    if (signal.aborted) return;

    const [filesRes, trackersRes] = await Promise.allSettled([
      fetchJson<TorrentFileOut[]>(`/torrents/${id}/files`, { signal }),
      fetchJson<TorrentTrackerOut[]>(`/torrents/${id}/trackers`, { signal }),
    ]);
    if (signal.aborted) return;
    const files = filesRes.status === "fulfilled" ? filesRes.value : [];
    const trackers = trackersRes.status === "fulfilled" ? trackersRes.value : [];

    const active = isActivelyDownloading(data);
    metaEl.replaceChildren(
      el("span", { className: active ? "live-dot" : "live-dot live-dot--paused" }),
      document.createTextNode(`Обновлено ${formatTime(new Date())}`),
    );

    const progress = data.runtime?.progress ?? 0;
    const pct = Math.round(progress * 1000) / 10;

    if (container.childElementCount > 0) saveDetailSpoilerStateFromDom(container, id);
    container.replaceChildren();
    const hero = el("section", { className: "detail-hero panel" });
    const body = el("div", { className: "panel__body" });
    hero.append(body);

    const bar = el("div", { className: "progress" });
    bar.append(
      el("div", {
        className: `progress__bar${pct >= 100 ? " progress__bar--complete" : ""}`,
        style: `width:${pct}%`,
      }),
    );

    const grid = el("div", { className: "detail-grid" });
    const addStat = (label: string, value: string) => {
      const box = el("div", { className: "stat-box" });
      box.append(el("div", { className: "stat-box__label" }, [label]), el("div", { className: "stat-box__value" }, [value]));
      grid.append(box);
    };
    addStat("Прогресс", fmtPercent(data.runtime?.progress));
    addStat("Размер", fmtBytes(data.runtime?.size));
    addStat("Скачивание", fmtRate(data.runtime?.download_rate));
    addStat("Отдача", fmtRate(data.runtime?.upload_rate));
    addStat("Отдано всего", fmtBytes(data.runtime?.total_uploaded));
    addStat("Скачано всего", fmtBytes(data.runtime?.downloaded));
    addStat("Рейтинг", fmtRatio(data.runtime?.ratio));
    addStat("Сиды / пиры", `${data.runtime?.num_seeds ?? 0} / ${data.runtime?.peers ?? 0}`);
    addStat("ETA", fmtEta(data.runtime?.eta));
    addStat("Лимиты ↓/↑", `${fmtLimit(data.runtime?.download_limit)} / ${fmtLimit(data.runtime?.upload_limit)}`);
    addStat("Папка", data.save_path);
    addStat("Метка", data.label || "—");
    addStat("Статус", displayStatusLabel(data));

    const backRefresh = () => loadDetail(id, container, metaEl, scheduleNext);

    const labelRow = el("div", { className: "label-edit-row" });
    const labelInput = el("input", {
      type: "text",
      placeholder: "Метка",
      value: data.label || "",
    }) as HTMLInputElement;
    const labelSave = el("button", { type: "button", className: "btn btn--sm" }, ["Сохранить метку"]);
    labelSave.addEventListener("click", async () => {
      labelSave.disabled = true;
      try {
        await fetchJson(`/torrents/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ label: labelInput.value.trim() }),
        });
        showToast("Метка сохранена");
        await backRefresh();
      } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), true);
      } finally {
        labelSave.disabled = false;
      }
    });
    labelRow.append(labelInput, labelSave);

    const actions = el("div", { className: "btn-row" });
    const pauseBtn = el("button", { type: "button", className: "btn" }, ["Пауза"]);
    const resumeBtn = el("button", { type: "button", className: "btn btn--primary" }, ["Старт"]);
    const recheckBtn = el("button", { type: "button", className: "btn" }, ["Проверить"]);
    const reannounceBtn = el("button", { type: "button", className: "btn" }, ["Переанонс"]);
    const delBtn = el("button", { type: "button", className: "btn btn--danger" }, ["Удалить"]);

    recheckBtn.addEventListener("click", async () => {
      recheckBtn.disabled = true;
      try {
        await postAction(`/torrents/${id}/recheck`);
        showToast("Запущена проверка хеша");
        await backRefresh();
      } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), true);
        recheckBtn.disabled = false;
      }
    });
    reannounceBtn.addEventListener("click", async () => {
      reannounceBtn.disabled = true;
      try {
        await postAction(`/torrents/${id}/reannounce`);
        showToast("Переанонс отправлен");
      } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), true);
      } finally {
        reannounceBtn.disabled = false;
      }
    });

    pauseBtn.addEventListener("click", async () => {
      try {
        await fetchJson(`/torrents/${id}/pause`, { method: "POST" });
        await backRefresh();
      } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), true);
      }
    });
    resumeBtn.addEventListener("click", async () => {
      try {
        await fetchJson(`/torrents/${id}/resume`, { method: "POST" });
        await backRefresh();
      } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), true);
      }
    });
    delBtn.addEventListener("click", () => {
      void deleteTorrentWithDialog({ id: data.id, display_name: data.display_name }, () => {
        setHashList();
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      });
    });
    if (data.status === "paused") pauseBtn.disabled = true;
    else resumeBtn.disabled = true;
    actions.append(pauseBtn, resumeBtn, recheckBtn, reannounceBtn, delBtn);

    body.append(
      el("span", { className: badgeClass(effectiveStatus(data)) }, [displayStatusLabel(data)]),
      el("h1", {}, [data.display_name || `Торрент #${data.id}`]),
      bar,
      grid,
      labelRow,
      actions,
      buildLimitsForm(data, () => void backRefresh()),
      buildFilesSpoiler(files, id, () => void backRefresh()),
      buildTrackersSpoiler(trackers, id, () => void backRefresh()),
      buildPeersSpoiler(data.peer_list ?? [], id),
      (() => {
        const pre = el("pre", {});
        pre.textContent = [
          data.magnet_uri ? `Magnet:\n${data.magnet_uri}` : "Magnet: —",
          data.info_hash ? `\n\nInfo hash: ${data.info_hash}` : "",
          data.runtime ? `\n\nДвижок: ${data.runtime.lt_state ?? data.runtime.runtime_status}` : "",
        ].join("");
        const metaBlock = buildDetailsSpoiler(
          "Подробности",
          el("div", { className: "details-block__content" }, [pre]),
        );
        applyDetailSpoilerState(metaBlock, id, "meta");
        return metaBlock;
      })(),
    );
    container.append(hero);
    scheduleNext?.(data);
  } catch (e) {
    if (signal.aborted) return;
    showToast(e instanceof Error ? e.message : String(e), true);
  }
}

function scheduleDetailPoll(
  id: number,
  container: HTMLElement,
  metaEl: HTMLElement,
  data: TorrentDetailOut,
): void {
  if (detailPollTimer !== null) clearTimeout(detailPollTimer);
  const ms = pickDetailPollMs(data);
  if (ms <= 0) return;
  const r = parseRoute();
  if (r.view !== "detail" || r.id !== id) return;
  detailPollTimer = setTimeout(() => {
    void loadDetail(id, container, metaEl, (next) => scheduleDetailPoll(id, container, metaEl, next));
  }, ms);
}

function mountDetailShell(root: HTMLElement, id: number): void {
  const metaEl = el("div", { className: "app-header__meta" });
  const main = el("div", { className: "detail-body" });

  const back = el("a", { href: "#", className: "back-link" }, ["← Назад к списку"]);
  back.addEventListener("click", (ev) => {
    ev.preventDefault();
    setHashList();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  root.append(
    back,
    el("header", { className: "app-header" }, [
      el("div", {}, [el("h1", {}, ["Торрент"]), el("p", { className: "field__hint" }, [`#${id}`])]),
      metaEl,
    ]),
    main,
  );

  const run = () =>
    loadDetail(id, main, metaEl, (data) => scheduleDetailPoll(id, main, metaEl, data));

  document.addEventListener("visibilitychange", () => {
    if (parseRoute().view !== "detail") return;
    if (!document.hidden) void run();
    else if (detailPollTimer !== null) {
      clearTimeout(detailPollTimer);
      detailPollTimer = null;
    }
  });

  void run();
}

function mountSettingsShell(root: HTMLElement): void {
  const back = navLink("← Назад к списку", () => setHashList());

  const header = el("header", { className: "app-header" }, [
    el("div", {}, [
      el("h1", {}, ["Настройки"]),
      el("p", { className: "field__hint" }, ["Глобальные параметры платформы"]),
    ]),
  ]);

  const statsHost = el("div", { id: "settings-session-host" });

  const themePanel = el("section", { className: "panel" });
  themePanel.append(el("div", { className: "panel__head" }, ["Внешний вид"]));
  const themeBody = el("div", { className: "panel__body" });
  const themeSelect = el("select", { className: "select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["auto", "Тема: как в системе"],
    ["light", "Тема: светлая"],
    ["dark", "Тема: тёмная"],
  ]) {
    const o = el("option", { value: val }, [label]) as HTMLOptionElement;
    if (val === getThemeMode()) o.selected = true;
    themeSelect.append(o);
  }
  themeSelect.addEventListener("change", () => {
    const v = themeSelect.value;
    applyTheme(v === "light" || v === "dark" ? v : "auto");
  });
  themeBody.append(field("Тема оформления", themeSelect, "Сохраняется в этом браузере"));
  themePanel.append(themeBody);

  const limits = mountGlobalLimitsPanel();
  limits.setAttribute("open", "");

  root.append(back, header, statsHost, themePanel, limits);

  void loadSessionStats().then((s) => {
    statsHost.replaceChildren(mountSessionBar(s));
  });
}

function render(): void {
  clearViewPolls();
  const root = document.getElementById("app");
  if (!root) return;
  root.replaceChildren();
  const route = parseRoute();
  if (route.view === "list") mountListShell(root);
  else if (route.view === "settings") mountSettingsShell(root);
  else mountDetailShell(root, route.id);
}

document.title = "Раздача";
applyTheme(getThemeMode());
window.addEventListener("hashchange", () => render());
render();
