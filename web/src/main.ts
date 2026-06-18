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
  download_limit?: number | null;
  upload_limit?: number | null;
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
  private?: boolean | null;
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

type HealthStatus = "ok" | "warn" | "down";
type HealthComponent = {
  id: string;
  name: string;
  kind: "core" | "engine";
  status: HealthStatus;
  detail?: string | null;
  latency_ms?: number | null;
  engine_id?: string | null;
  url?: string | null;
  tls?: boolean;
  meta?: Record<string, unknown> | null;
};
type HealthFull = {
  status: HealthStatus;
  generated_at: string;
  summary: { engines_ok: number; engines_total: number };
  components: HealthComponent[];
};

type TorrentDetailOut = TorrentOut & { runtime: RuntimeOut | null; peer_list?: TorrentPeerOut[] };
type Route = { view: "list" } | { view: "detail"; id: number } | { view: "settings" };
type DeleteTorrentChoice = "cancel" | "torrent_only" | "torrent_and_files";

let listPollTimer: ReturnType<typeof setTimeout> | null = null;
let listStream: EventSource | null = null;
let detailPollTimer: ReturnType<typeof setTimeout> | null = null;
let settingsHealthTimer: ReturnType<typeof setTimeout> | null = null;
let listAbort: AbortController | null = null;
let detailAbort: AbortController | null = null;
let listLoadGeneration = 0;
let lastListItems: TorrentOut[] = [];
let toastTimer: ReturnType<typeof setTimeout> | null = null;
let selectedIds = new Set<number>();
let selectionChanged: (() => void) | null = null;

type Role = "viewer" | "operator" | "admin";
type MeOut = { name: string; role: Role; source: string };
let currentRole: Role | null = null;
let currentMe: MeOut | null = null;

type SettingsTab = "info" | "users" | "limits" | "logs";
let activeSettingsTab: SettingsTab = "info";

function canWrite(): boolean {
  return currentRole === "operator" || currentRole === "admin";
}
function isAdmin(): boolean {
  return currentRole === "admin";
}

function getApiKey(): string {
  return lsGet("seedingApiKey") ?? "";
}
function setApiKey(key: string): void {
  lsSet("seedingApiKey", key);
}

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
  if (settingsHealthTimer !== null) {
    clearTimeout(settingsHealthTimer);
    settingsHealthTimer = null;
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
      if (!document.querySelector(".login-overlay")) showLoginDialog();
      throw new Error("Нужен API-ключ");
    }
    if (res.status === 403) {
      throw new Error("Доступ запрещён");
    }
    if (res.status === 503) {
      let msg = "Идёт обслуживание, подождите…";
      try {
        const b = JSON.parse(await res.text()) as { error?: { message?: string } };
        if (b.error?.message) msg = b.error.message;
      } catch {
        /* ignore */
      }
      throw new Error(msg);
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

const ICON_PATHS: Record<string, string> = {
  refresh:
    '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
};

/** Инлайновая SVG-иконка (Feather-стиль), наследует цвет текста кнопки. */
function icon(name: keyof typeof ICON_PATHS): HTMLElement {
  const span = el("span", { className: "icon", "aria-hidden": "true" });
  span.innerHTML =
    `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ` +
    `stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name]}</svg>`;
  return span;
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

function statChip(value: string, label: string, accent?: "dl" | "ul"): HTMLElement {
  return el("div", { className: `stat-chip${accent ? ` stat-chip--${accent}` : ""}` }, [
    el("div", { className: "stat-chip__value" }, [value]),
    el("div", { className: "stat-chip__label" }, [label]),
  ]);
}

function mountSessionBar(stats: SessionStats | null): HTMLElement {
  const bar = el("div", { className: "session-bar" });
  if (!stats) {
    bar.append(el("span", { className: "session-bar__muted" }, ["Статистика недоступна"]));
    return bar;
  }
  const enginesNote =
    stats.engines_total != null
      ? `${stats.engines_ok ?? 0}/${stats.engines_total} движков`
      : "";
  bar.append(
    statChip(`${stats.torrents}`, `Раздач · ${stats.torrents_active} актив.`),
    statChip(`↓ ${fmtRate(stats.download_rate)}`, "Скачивание", "dl"),
    statChip(`↑ ${fmtRate(stats.upload_rate)}`, "Отдача", "ul"),
    statChip(fmtBytes(stats.total_uploaded), "Всего отдано"),
  );
  if (enginesNote) bar.append(statChip(enginesNote, "Онлайн"));
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

function mountEngineLimitsPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, ["Лимиты движков"]);
  const refreshBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Обновить"]);
  head.append(refreshBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const hint = el("p", { className: "field__hint" }, [
    "Постоянные лимиты сессии каждого движка (КБ/с, 0 = без ограничения). Сохраняются и переживают перезапуск движка.",
  ]);
  const list = el("div", { className: "keys-list" });
  body.append(hint, list);
  panel.append(body);

  const toKb = (v?: number | null) => (v && v > 0 ? String(Math.round(v / 1024)) : "");

  const reload = async () => {
    try {
      const engines = await fetchJson<EngineOut[]>("/engines");
      list.replaceChildren();
      if (engines.length === 0) {
        list.append(el("p", { className: "field__hint" }, ["Движков нет"]));
        return;
      }
      for (const e of engines) {
        const row = el("div", { className: `key-row${e.online === false ? " key-row--off" : ""}` });
        const free = e.disk_free != null ? `своб. ${fmtBytes(e.disk_free)}` : "место неизв.";
        const off = e.online === false ? " · офлайн" : "";
        const meta = el("div", { className: "key-row__meta" }, [
          el("span", { className: "key-row__name" }, [e.id]),
          el("span", { className: "key-row__sub" }, [`${free}${off}`]),
        ]);
        const dl = el("input", {
          type: "number",
          min: "0",
          placeholder: "∞",
          value: toKb(e.download_limit),
        }) as HTMLInputElement;
        const ul = el("input", {
          type: "number",
          min: "0",
          placeholder: "∞",
          value: toKb(e.upload_limit),
        }) as HTMLInputElement;
        const apply = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["ОК"]);
        const parse = (s: string) => {
          const n = Number(s.trim());
          return Number.isFinite(n) && n > 0 ? Math.round(n * 1024) : 0;
        };
        apply.addEventListener("click", async () => {
          apply.disabled = true;
          try {
            await fetchJson(`/engines/${encodeURIComponent(e.id)}/limits`, {
              method: "POST",
              body: JSON.stringify({ download_limit: parse(dl.value), upload_limit: parse(ul.value) }),
            });
            showToast(`Лимиты движка ${e.id} применены`);
            await reload();
          } catch (err) {
            showToast(err instanceof Error ? err.message : String(err), true);
          } finally {
            apply.disabled = false;
          }
        });
        const controls = el("div", { className: "engine-limits__controls" }, [
          el("label", { className: "limits-form__field" }, ["↓ КБ/с", dl]),
          el("label", { className: "limits-form__field" }, ["↑ КБ/с", ul]),
          apply,
        ]);
        row.append(meta, controls);
        list.append(row);
      }
    } catch (e) {
      list.replaceChildren(el("p", { className: "field__hint" }, [e instanceof Error ? e.message : String(e)]));
    }
  };

  refreshBtn.addEventListener("click", () => void reload());
  void reload();
  return panel;
}

type QuotaItem = {
  label: string;
  upload_quota: number | null;
  uploaded_total: number;
  enabled: boolean;
  exceeded: boolean;
  percent: number | null;
  paused_count: number;
  since: string | null;
};

const GIB = 1024 * 1024 * 1024;

type NetSettingsOut = { dht: boolean; pex: boolean; lsd: boolean; applied?: number; errors?: number };

function mountNetSettingsPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["Поиск пиров (глобально)"]));
  const body = el("div", { className: "panel__body" });
  const hint = el("p", { className: "field__hint" }, [
    "Глобально включает/выключает источники пиров на всех движках. DHT и LSD — настройки сессии libtorrent; PEX управляется по каждой раздаче. Приватные раздачи остаются без DHT/PEX/LSD независимо от этих переключателей.",
  ]);

  const mkToggle = (key: "dht" | "pex" | "lsd", label: string, desc: string) => {
    const input = el("input", { type: "checkbox" }) as HTMLInputElement;
    input.dataset.key = key;
    const row = el("label", { className: "net-toggle" }, [
      input,
      el("span", { className: "net-toggle__text" }, [
        el("span", { className: "net-toggle__title" }, [label]),
        el("span", { className: "net-toggle__desc" }, [desc]),
      ]),
    ]);
    return { row, input };
  };

  const dht = mkToggle("dht", "DHT", "Распределённый поиск пиров (глобальная сеть).");
  const pex = mkToggle("pex", "PEX", "Обмен списками пиров между подключёнными.");
  const lsd = mkToggle("lsd", "LSD", "Поиск пиров в локальной сети (multicast).");
  const toggles = el("div", { className: "net-toggles" }, [dht.row, pex.row, lsd.row]);

  const saveBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Применить"]);
  const result = el("p", { className: "field__hint" }, [""]);

  const setAll = (s: NetSettingsOut) => {
    dht.input.checked = s.dht;
    pex.input.checked = s.pex;
    lsd.input.checked = s.lsd;
  };

  const load = async () => {
    try {
      setAll(await fetchJson<NetSettingsOut>("/settings/net"));
    } catch {
      /* ignore */
    }
  };

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    result.textContent = "Применяю…";
    try {
      const s = await fetchJson<NetSettingsOut>("/settings/net", {
        method: "POST",
        body: JSON.stringify({ dht: dht.input.checked, pex: pex.input.checked, lsd: lsd.input.checked }),
      });
      setAll(s);
      result.textContent = `Применено на движках: ${s.applied ?? 0}${s.errors ? `, ошибок: ${s.errors}` : ""}.`;
      showToast("Настройки поиска пиров применены");
    } catch (e) {
      result.textContent = e instanceof Error ? e.message : String(e);
      showToast(result.textContent, true);
    } finally {
      saveBtn.disabled = false;
    }
  });

  body.append(hint, toggles, el("div", { className: "btn-row" }, [saveBtn]), result);
  panel.append(body);
  void load();
  return panel;
}

function mountPrivateMaintenancePanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["Приватные трекеры"]));
  const body = el("div", { className: "panel__body" });
  const hint = el("p", { className: "field__hint" }, [
    "Прогнать все раздачи и автоматически отключить DHT/PEX/LSD там, где трекер приватный (флаг private или passkey в адресе). Применяется к уже запущенным раздачам.",
  ]);
  const btn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, [
    "Применить приватный режим ко всем",
  ]);
  const result = el("p", { className: "field__hint" }, [""]);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    result.textContent = "Обрабатываю…";
    try {
      const r = await fetchJson<{ checked: number; applied: number; private: number; errors: number }>(
        "/torrents/maintenance/reapply-private",
        { method: "POST" },
      );
      result.textContent = `Проверено ${r.checked}, приватных ${r.private}, ошибок ${r.errors}.`;
      showToast("Приватный режим применён");
    } catch (e) {
      result.textContent = e instanceof Error ? e.message : String(e);
      showToast(result.textContent, true);
    } finally {
      btn.disabled = false;
    }
  });
  body.append(hint, btn, result);
  panel.append(body);
  return panel;
}

function mountQuotasPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, ["Квоты по меткам"]);
  const refreshBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Обновить"]);
  head.append(refreshBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const hint = el("p", { className: "field__hint" }, [
    "Лимит суммарной отдачи на метку (ГиБ). При достижении активные раздачи метки ставятся на паузу; «Сбросить» обнуляет счётчик и возобновляет их.",
  ]);

  const labelSelect = el("select", { className: "select" }) as HTMLSelectElement;
  const quotaInput = el("input", { type: "number", min: "0", step: "0.1", placeholder: "ГиБ" }) as HTMLInputElement;
  const addBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Задать"]);
  const form = el("div", { className: "keys-form" }, [labelSelect, quotaInput, addBtn]);
  const list = el("div", { className: "keys-list" });
  body.append(hint, form, list);
  panel.append(body);

  const loadLabels = async () => {
    try {
      const labels = await fetchJson<string[]>("/labels");
      labelSelect.replaceChildren();
      if (labels.length === 0) {
        labelSelect.append(el("option", { value: "" }, ["Нет меток"]));
        return;
      }
      for (const l of labels) labelSelect.append(el("option", { value: l }, [l]));
    } catch {
      labelSelect.replaceChildren(el("option", { value: "" }, ["Ошибка загрузки меток"]));
    }
  };

  const reload = async () => {
    try {
      const rows = await fetchJson<QuotaItem[]>("/quotas");
      list.replaceChildren();
      if (rows.length === 0) {
        list.append(el("p", { className: "field__hint" }, ["Квоты не заданы"]));
        return;
      }
      for (const q of rows) {
        const row = el("div", { className: `key-row${q.enabled ? "" : " key-row--off"}` });
        const quotaStr = q.upload_quota ? fmtBytes(q.upload_quota) : "∞";
        const pct = q.percent != null ? Math.min(100, q.percent) : 0;
        const bar = el("div", { className: "quota-bar" }, [
          el("div", {
            className: `quota-bar__fill${q.exceeded ? " quota-bar__fill--over" : ""}`,
            style: `width:${pct}%`,
          }),
        ]);
        const meta = el("div", { className: "key-row__meta quota-meta" }, [
          el("span", { className: "key-row__name" }, [
            q.label,
            q.exceeded ? el("span", { className: "audit-status audit-status--err quota-badge" }, ["лимит"]) : "",
          ]),
          el("span", { className: "key-row__sub" }, [
            `${fmtBytes(q.uploaded_total)} / ${quotaStr}${q.percent != null ? ` · ${q.percent}%` : ""}${
              q.paused_count ? ` · на паузе: ${q.paused_count}` : ""
            }`,
          ]),
          bar,
        ]);
        const toggle = el("button", { type: "button", className: "btn btn--sm" }, [
          q.enabled ? "Выключить" : "Включить",
        ]);
        toggle.addEventListener("click", async () => {
          try {
            await fetchJson("/quotas", {
              method: "POST",
              body: JSON.stringify({ label: q.label, upload_quota: q.upload_quota ?? 0, enabled: !q.enabled }),
            });
            await reload();
          } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), true);
          }
        });
        const reset = el("button", { type: "button", className: "btn btn--sm" }, ["Сбросить"]);
        reset.addEventListener("click", async () => {
          if (!window.confirm(`Сбросить счётчик метки «${q.label}» и возобновить приостановленные раздачи?`)) return;
          try {
            const r = await fetchJson<{ resumed: number }>(`/quotas/${encodeURIComponent(q.label)}/reset`, {
              method: "POST",
            });
            showToast(`Счётчик сброшен, возобновлено: ${r.resumed}`);
            await reload();
          } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), true);
          }
        });
        const del = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["Удалить"]);
        del.addEventListener("click", async () => {
          if (!window.confirm(`Удалить квоту метки «${q.label}»?`)) return;
          try {
            await fetchDelete(`/quotas/${encodeURIComponent(q.label)}`);
            await reload();
          } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), true);
          }
        });
        row.append(meta, el("div", { className: "btn-row" }, [toggle, reset, del]));
        list.append(row);
      }
    } catch (e) {
      list.replaceChildren(el("p", { className: "field__hint" }, [e instanceof Error ? e.message : String(e)]));
    }
  };

  addBtn.addEventListener("click", async () => {
    const label = labelSelect.value;
    if (!label) {
      showToast("Выберите метку", true);
      return;
    }
    const gib = Number(quotaInput.value.trim());
    const bytes = Number.isFinite(gib) && gib > 0 ? Math.round(gib * GIB) : 0;
    addBtn.disabled = true;
    try {
      await fetchJson("/quotas", {
        method: "POST",
        body: JSON.stringify({ label, upload_quota: bytes, enabled: true }),
      });
      quotaInput.value = "";
      showToast(`Квота для «${label}» задана`);
      await reload();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      addBtn.disabled = false;
    }
  });

  refreshBtn.addEventListener("click", () => void reload());
  void loadLabels();
  void reload();
  return panel;
}

function effectiveStatus(t: TorrentOut | TorrentDetailOut): string {
  const rs = (t.runtime?.runtime_status || "").toLowerCase();
  const lt = (t.runtime?.lt_state || "").toLowerCase();
  const progress = t.runtime?.progress;
  if (t.status === "migrating") return "migrating";
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
    migrating: "Перенос",
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
  if (status === "migrating") return "badge badge--migrating";
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

type MigrateStatusOut = {
  id: number;
  active: boolean;
  resumable?: boolean;
  attempts?: number;
  transport?: string | null;
  phase: string;
  progress: number | null;
  copied?: number | null;
  total?: number | null;
  speed?: number | null;
  eta?: number | null;
  message?: string | null;
};

const MIGRATE_PHASE_LABELS: Record<string, string> = {
  preparing: "Подготовка…",
  queued: "В очереди…",
  copying: "Копирование файлов",
  checking: "Проверка хэша",
  finalizing: "Завершение…",
  migrating: "Перенос…",
  done: "Готово",
  error: "Ошибка переноса",
};

function buildMigrateProgress(data: TorrentDetailOut, onDone: () => void): HTMLElement {
  const wrap = el("div", { className: "migrate-progress migrate-progress--indeterminate" });
  const label = el("div", { className: "migrate-progress__label" }, ["Подготовка…"]);
  const track = el("div", { className: "progress" });
  const fill = el("div", { className: "progress__bar" });
  track.append(fill);
  wrap.append(label, track);

  let timer = 0;
  const stop = () => {
    if (timer) window.clearInterval(timer);
    timer = 0;
  };
  const tick = async () => {
    if (!document.body.contains(wrap)) {
      stop();
      return;
    }
    try {
      const s = await fetchJson<MigrateStatusOut>(`/torrents/${data.id}/migrate-status`);
      const phase = s.phase || "migrating";
      const pct = typeof s.progress === "number" ? Math.round(s.progress * 100) : null;
      let text = MIGRATE_PHASE_LABELS[phase] ?? phase;
      const tname: Record<string, string> = { media: "общий /media", http: "через оркестратор", direct: "напрямую" };
      if (s.transport && phase !== "error" && phase !== "done") text += ` · ${tname[s.transport] ?? s.transport}`;
      if (pct != null && (phase === "copying" || phase === "checking")) text += ` · ${pct}%`;
      if (phase === "copying" && s.total) text += `  (${fmtBytes(s.copied)} / ${fmtBytes(s.total)})`;
      if (phase === "copying" && s.speed) text += ` · ${fmtBytes(s.speed)}/с`;
      if (phase === "copying" && s.eta) text += ` · ост. ${fmtEta(s.eta)}`;
      if (phase === "error" && s.message) text += ` — ${s.message}`;
      label.textContent = text;
      const indeterminate = pct == null && phase !== "error" && phase !== "done";
      wrap.classList.toggle("migrate-progress--indeterminate", indeterminate);
      wrap.classList.toggle("migrate-progress--error", phase === "error");
      fill.style.width = pct != null ? `${pct}%` : "100%";
      if (!s.active) {
        stop();
        window.setTimeout(() => onDone(), 1000);
      }
    } catch {
      // транзиентная ошибка опроса — попробуем на следующем тике
    }
  };
  void tick();
  timer = window.setInterval(() => void tick(), 1000);
  return wrap;
}

function buildResumableRow(
  data: TorrentDetailOut,
  status: MigrateStatusOut,
  onChanged: () => void,
): HTMLElement {
  const wrap = el("div", { className: "migrate-row migrate-row--resumable" });
  const pct = typeof status.progress === "number" ? ` (${Math.round(status.progress * 100)}%)` : "";
  const attempts = status.attempts ? ` · попыток: ${status.attempts}` : "";
  const msg = status.message ? `: ${status.message}` : "";
  const resumeBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Возобновить"]);
  const cancelBtn = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["Отменить"]);
  resumeBtn.addEventListener("click", async () => {
    resumeBtn.disabled = true;
    cancelBtn.disabled = true;
    try {
      await postAction(`/torrents/${data.id}/migrate/resume`);
      showToast("Перенос возобновлён");
      onChanged();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
      resumeBtn.disabled = false;
      cancelBtn.disabled = false;
    }
  });
  cancelBtn.addEventListener("click", async () => {
    if (!window.confirm("Отменить перенос и удалить частичную копию на целевом движке?")) return;
    resumeBtn.disabled = true;
    cancelBtn.disabled = true;
    try {
      await postAction(`/torrents/${data.id}/migrate/cancel`);
      showToast("Перенос отменён");
      onChanged();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
      resumeBtn.disabled = false;
      cancelBtn.disabled = false;
    }
  });
  wrap.append(
    el("span", { className: "migrate-row__label migrate-row__label--warn" }, [
      `Перенос прерван${pct}${attempts}${msg}`,
    ]),
    el("div", { className: "btn-row" }, [resumeBtn, cancelBtn]),
  );
  return wrap;
}

function buildMigrateRow(data: TorrentDetailOut, onStarted: () => void): HTMLElement {
  if (data.status === "migrating") return buildMigrateProgress(data, onStarted);
  const host = el("div", { className: "migrate-host" });
  // Если есть прерванный перенос — предложить возобновить/отменить вместо выбора движка.
  void (async () => {
    try {
      const s = await fetchJson<MigrateStatusOut>(`/torrents/${data.id}/migrate-status`);
      if (s.resumable && document.body.contains(host)) {
        host.replaceChildren(buildResumableRow(data, s, onStarted));
      }
    } catch {
      // нет джоба/ошибка — оставляем обычный выбор движка
    }
  })();
  const wrap = el("div", { className: "migrate-row" });
  const select = el("select", { className: "select" }) as HTMLSelectElement;
  select.append(el("option", { value: "" }, ["Загрузка движков…"]));
  const btn = el("button", { type: "button", className: "btn btn--sm" }, ["Перенести"]) as HTMLButtonElement;
  btn.disabled = true;

  const migrating = data.status === "migrating";
  if (migrating) {
    select.replaceChildren(el("option", { value: "" }, ["Перенос выполняется…"]));
    select.disabled = true;
  } else {
    void (async () => {
      try {
        const engines = await fetchJson<EngineOut[]>("/engines");
        select.replaceChildren();
        const targets = engines.filter((e) => e.id !== data.engine_id);
        if (targets.length === 0) {
          select.append(el("option", { value: "" }, ["Нет других движков"]));
          return;
        }
        select.append(el("option", { value: "" }, ["Выберите целевой движок…"]));
        for (const e of targets) {
          const free = e.disk_free != null ? `своб. ${fmtBytes(e.disk_free)}` : "место неизв.";
          const off = e.online === false ? " — офлайн" : "";
          const opt = el("option", { value: e.id }, [`${e.id} · ${free}${off}`]) as HTMLOptionElement;
          if (e.online === false) opt.disabled = true;
          select.append(opt);
        }
        btn.disabled = true;
      } catch (e) {
        select.replaceChildren(el("option", { value: "" }, ["Ошибка загрузки движков"]));
        showToast(e instanceof Error ? e.message : String(e), true);
      }
    })();
  }

  select.addEventListener("change", () => {
    btn.disabled = !select.value;
  });

  btn.addEventListener("click", async () => {
    const target = select.value;
    if (!target) return;
    btn.disabled = true;
    try {
      await postAction(`/torrents/${data.id}/migrate?engine_id=${encodeURIComponent(target)}`);
      showToast(`Перенос на ${target} запущен`);
      onStarted();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
      btn.disabled = false;
    }
  });

  wrap.append(
    el("span", { className: "migrate-row__label" }, [`Движок: ${data.engine_id}`]),
    select,
    btn,
  );
  host.append(wrap);
  return host;
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

function buildPrivateRow(data: TorrentDetailOut, onApplied: () => void): HTMLElement {
  const wrap = el("div", { className: "private-form" });
  const on = data.runtime?.private === true;
  const status = el("span", { className: `private-state${on ? " private-state--on" : ""}` }, [
    on ? "Приватный: DHT/PEX/LSD выключены" : "Публичный: DHT/PEX/LSD включены",
  ]);
  const hint = el("p", { className: "field__hint" }, [
    "Для приватных трекеров (passkey) держите режим включённым — иначе libtorrent тянет мусорные пиры из DHT.",
  ]);
  const toggleBtn = el("button", { type: "button", className: "btn btn--sm" }, [
    on ? "Выключить приватный режим" : "Включить приватный режим",
  ]);
  const autoBtn = el("button", { type: "button", className: "btn btn--sm btn--ghost" }, ["Авто"]);
  const apply = async (enabled: boolean | null) => {
    toggleBtn.disabled = true;
    autoBtn.disabled = true;
    try {
      await fetchJson(`/torrents/${data.id}/private`, {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      showToast("Приватный режим обновлён");
      onApplied();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
      toggleBtn.disabled = false;
      autoBtn.disabled = false;
    }
  };
  toggleBtn.addEventListener("click", () => void apply(!on));
  autoBtn.addEventListener("click", () => void apply(null));
  wrap.append(status, el("div", { className: "private-form__row" }, [toggleBtn, autoBtn]), hint);
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
  const topChildren: (string | Node)[] = canWrite() ? [checkbox, title, topRight] : [title, topRight];
  card.append(el("div", { className: "torrent-card__top" }, topChildren), bar, stats);
  if (canWrite()) card.append(actions);
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
    syncReset();
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
    syncReset();
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
    syncReset();
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
    syncReset();
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

  const settingsLink = el("button", { type: "button", className: "btn btn--ghost btn--sm" }, [icon("settings"), "Настройки"]);
  settingsLink.addEventListener("click", () => {
    setHashSettings();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  const headerActions = el("div", { className: "app-header__actions" });
  if (canWrite()) headerActions.append(addTorrentBtn);
  headerActions.append(settingsLink, metaEl);
  const header = el("header", { className: "app-header" }, [
    el("div", {}, [el("h1", {}, ["Раздача"]), el("p", { className: "field__hint" }, ["Управление торрентами"])]),
    headerActions,
  ]);

  const resetFilters = el("button", {
    type: "button",
    className: "btn btn--ghost btn--sm list-controls__reset",
    title: "Сбросить фильтры",
  }, ["Сброс"]);
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
    syncReset();
  });
  function syncReset(): void {
    const active = Boolean(listSearch || listStatusFilter || listLabelFilter) || listSort !== "added";
    resetFilters.hidden = !active;
  }

  const refreshBtn = el("button", {
    type: "button",
    className: "btn btn--ghost btn--sm list-controls__refresh",
    title: "Обновить",
  }, [icon("refresh")]);
  refreshBtn.addEventListener("click", () => void refresh());

  // Один ряд: фильтры слева, справа — счётчик, сброс и обновление.
  const filters = el("div", { className: "list-controls" }, [
    searchInput,
    statusSelect,
    labelSelect,
    sortSelect,
    densitySelect,
    el("span", { className: "list-controls__spacer" }),
    countEl,
    resetFilters,
    refreshBtn,
    labelSuggestions,
  ]);
  applyDensity();

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
    bulkBar,
    listHost,
  );
  syncReset();

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

    // Не перерисовываем дерево, пока пользователь работает с контролом
    // (открыт выпадающий список движков, ввод метки/лимитов) — иначе он закроется/сбросится.
    const activeEl = document.activeElement as HTMLElement | null;
    if (
      container.childElementCount > 0 &&
      activeEl &&
      container.contains(activeEl) &&
      ["SELECT", "INPUT", "TEXTAREA"].includes(activeEl.tagName)
    ) {
      scheduleNext?.(data);
      return;
    }

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

    const backRefresh = () => loadDetail(id, container, metaEl, scheduleNext);
    const st = effectiveStatus(data);
    const migrating = data.status === "migrating";

    let title = data.display_name || `Торрент #${data.id}`;
    if (title.toLowerCase().endsWith(".torrent")) title = title.slice(0, -".torrent".length);

    const head = el("div", { className: "detail-head" }, [
      el("span", { className: badgeClass(st) }, [displayStatusLabel(data)]),
      el("h1", {}, [title]),
    ]);

    // Статичные факты — компактной подстрокой, без отдельных боксов.
    const subParts = [`#${data.id}`, `движок ${data.engine_id}`, fmtBytes(data.runtime?.size)];
    const ratioStr = fmtRatio(data.runtime?.ratio);
    if (ratioStr !== "—") subParts.push(`рейтинг ${ratioStr}`);
    if (data.runtime?.added_time) {
      subParts.push(`добавлено ${new Date(data.runtime.added_time * 1000).toLocaleDateString("ru-RU")}`);
    }
    const sub = el("div", { className: "detail-sub" }, [subParts.join("  ·  ")]);

    const progressWrap = el("div", { className: "detail-progress" });
    progressWrap.append(bar, el("span", { className: "detail-progress__pct" }, [fmtPercent(progress)]));
    if (st === "downloading") {
      const eta = fmtEta(data.runtime?.eta);
      if (eta !== "—") progressWrap.append(el("span", { className: "detail-progress__eta" }, [`ETA ${eta}`]));
    }

    // Живые показатели — чипами; «скачано» только если реально качали.
    const chips = el("div", { className: "detail-chips" });
    chips.append(
      statChip(`↓ ${fmtRate(data.runtime?.download_rate)}`, "Скачивание", "dl"),
      statChip(`↑ ${fmtRate(data.runtime?.upload_rate)}`, "Отдача", "ul"),
      statChip(`${data.runtime?.num_seeds ?? 0} / ${data.runtime?.peers ?? 0}`, "Сиды / пиры"),
      statChip(fmtBytes(data.runtime?.total_uploaded), "Отдано всего"),
    );
    const dl = data.runtime?.downloaded ?? 0;
    if (dl > 0) chips.append(statChip(fmtBytes(dl), "Скачано всего"));
    if (data.runtime?.private === true) chips.append(statChip("Приватная", "DHT/PEX/LSD выключены"));

    // Тулбар: основное действие (пауза/старт) + проверка/переанонс, удаление справа.
    const toolbar = el("div", { className: "detail-toolbar" });
    const toggleBtn = el("button", {
      type: "button",
      className: st === "paused" ? "btn btn--primary" : "btn",
    }, [st === "paused" ? "▶ Старт" : "⏸ Пауза"]);
    const recheckBtn = el("button", { type: "button", className: "btn" }, ["Проверить"]);
    const reannounceBtn = el("button", { type: "button", className: "btn" }, ["Переанонс"]);
    const delBtn = el("button", { type: "button", className: "btn btn--danger" }, ["Удалить"]);

    toggleBtn.addEventListener("click", async () => {
      toggleBtn.disabled = true;
      try {
        await fetchJson(`/torrents/${id}/${st === "paused" ? "resume" : "pause"}`, { method: "POST" });
        await backRefresh();
      } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), true);
        toggleBtn.disabled = false;
      }
    });
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
    delBtn.addEventListener("click", () => {
      void deleteTorrentWithDialog({ id: data.id, display_name: data.display_name }, () => {
        setHashList();
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      });
    });
    if (migrating) {
      for (const b of [toggleBtn, recheckBtn, reannounceBtn, delBtn]) b.disabled = true;
    }
    toolbar.append(
      toggleBtn,
      recheckBtn,
      reannounceBtn,
      el("span", { className: "detail-toolbar__spacer" }),
      delBtn,
    );

    // Метка — редактируемая прямо в карточке управления.
    const labelInput = el("input", {
      type: "text",
      placeholder: "Без метки",
      value: data.label || "",
    }) as HTMLInputElement;
    const labelSave = el("button", { type: "button", className: "btn btn--sm" }, ["Сохранить"]);
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
    const labelRow = el("div", { className: "manage-card__row" }, [labelInput, labelSave]);

    const manageCard = (heading: string, node: HTMLElement) =>
      el("div", { className: "manage-card" }, [
        el("div", { className: "manage-card__title" }, [heading]),
        node,
      ]);

    const manage = el("div", { className: "detail-manage" }, [
      manageCard("Метка", labelRow),
      manageCard("Лимиты скорости", buildLimitsForm(data, () => void backRefresh())),
      manageCard("Приватный режим", buildPrivateRow(data, () => void backRefresh())),
      manageCard("Перенести на движок", buildMigrateRow(data, () => void backRefresh())),
    ]);

    // Технические подробности — в сворачиваемом блоке (папка, hash, magnet).
    const detailsContent = el("div", { className: "details-block__content" });
    const dlist = el("dl", { className: "def-list" });
    const defRow = (k: string, v: string) => dlist.append(el("dt", {}, [k]), el("dd", {}, [v]));
    defRow("Папка", data.save_path || "—");
    defRow("Info hash", data.info_hash || "—");
    defRow("Состояние lt", data.runtime ? (data.runtime.lt_state ?? data.runtime.runtime_status ?? "—") : "—");
    detailsContent.append(dlist);
    if (data.magnet_uri) {
      const pre = el("pre", { className: "def-list__magnet" });
      pre.textContent = data.magnet_uri;
      detailsContent.append(pre);
    }
    const metaBlock = buildDetailsSpoiler("Подробности", detailsContent);
    applyDetailSpoilerState(metaBlock, id, "meta");

    body.append(head, sub, progressWrap, chips);
    if (canWrite()) body.append(toolbar, manage);
    body.append(
      buildFilesSpoiler(files, id, () => void backRefresh()),
      buildTrackersSpoiler(trackers, id, () => void backRefresh()),
      buildPeersSpoiler(data.peer_list ?? [], id),
      metaBlock,
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

const HEALTH_STATUS_LABEL: Record<HealthStatus, string> = {
  ok: "Норма",
  warn: "Внимание",
  down: "Сбой",
};
const HEALTH_OVERALL_LABEL: Record<HealthStatus, string> = {
  ok: "Все системы в норме",
  warn: "Есть предупреждения",
  down: "Обнаружен сбой",
};

function healthCard(c: HealthComponent): HTMLElement {
  const card = el("div", { className: `health-card health-card--${c.status}` });
  const top = el("div", { className: "health-card__top" }, [
    el("span", { className: `health-dot health-dot--${c.status}` }),
    el("span", { className: "health-card__name" }, [c.name]),
  ]);
  if (c.kind === "engine" && c.tls) {
    top.append(el("span", { className: "health-card__tag", title: "Шифрованное соединение" }, ["TLS"]));
  }
  card.append(top);
  const statusRow = el("div", { className: "health-card__status-row" }, [
    el("span", { className: "health-card__status" }, [HEALTH_STATUS_LABEL[c.status]]),
  ]);
  if (typeof c.latency_ms === "number") {
    statusRow.append(el("span", { className: "health-card__latency" }, [`${c.latency_ms} мс`]));
  }
  card.append(statusRow);
  if (c.detail) card.append(el("div", { className: "health-card__detail" }, [c.detail]));
  return card;
}

type AlertItem = { id: string; severity: string; title: string; message: string };
type AlertsOut = { generated_at: string; count: number; critical: number; alerts: AlertItem[] };

function mountAlertsPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, ["Уведомления"]);
  const refreshBtn = el("button", { type: "button", className: "btn btn--ghost btn--sm", title: "Обновить" }, [icon("refresh")]);
  head.append(refreshBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const list = el("div", { className: "alerts-list" }, ["Проверка…"]);
  body.append(list);
  panel.append(body);

  let busy = false;
  let timer: number | null = null;
  const load = async () => {
    if (busy) return;
    busy = true;
    try {
      const data = await fetchJson<AlertsOut>("/alerts");
      if (parseRoute().view !== "settings") return;
      list.replaceChildren();
      if (data.alerts.length === 0) {
        list.append(el("div", { className: "alerts-empty" }, ["✓ Активных уведомлений нет"]));
      } else {
        for (const a of data.alerts) {
          const sev = a.severity === "critical" ? "crit" : "warn";
          list.append(
            el("div", { className: `alert-row alert-row--${sev}` }, [
              el("span", { className: `alert-badge alert-badge--${sev}` }, [
                a.severity === "critical" ? "критично" : "внимание",
              ]),
              el("div", { className: "alert-text" }, [
                el("span", { className: "alert-title" }, [a.title]),
                el("span", { className: "alert-msg" }, [a.message]),
              ]),
            ]),
          );
        }
      }
    } catch (e) {
      list.replaceChildren(el("div", { className: "alerts-empty" }, [e instanceof Error ? e.message : String(e)]));
    } finally {
      busy = false;
      if (timer !== null) clearTimeout(timer);
      if (parseRoute().view === "settings" && !document.hidden) {
        timer = window.setTimeout(() => void load(), 30000);
      }
    }
  };
  refreshBtn.addEventListener("click", () => void load());
  void load();
  return panel;
}

type SysFs = { mount: string; total: number; used: number; free: number; pct: number | null };
type SysContainer = {
  name: string;
  full: string;
  cpu_pct?: number;
  mem_bytes?: number;
  io_read_bps?: number;
  io_write_bps?: number;
};
type SystemOut = {
  generated_at: string;
  available: boolean;
  reason?: string;
  host?: {
    cpu_pct: number | null;
    cpu_cores: number | null;
    load1: number | null;
    load5: number | null;
    load15: number | null;
    mem_total: number | null;
    mem_used: number | null;
    mem_pct: number | null;
    disk_read_bps: number;
    disk_write_bps: number;
    filesystems: SysFs[];
  };
  containers?: SysContainer[];
};

function sysStat(label: string, value: string, sub?: string): HTMLElement {
  return el("div", { className: "sys-stat" }, [
    el("span", { className: "sys-stat__label" }, [label]),
    el("span", { className: "sys-stat__value" }, [value]),
    ...(sub ? [el("span", { className: "sys-stat__sub" }, [sub])] : []),
  ]);
}

function meterBar(pct: number | null): HTMLElement {
  const p = Math.max(0, Math.min(100, pct ?? 0));
  const tone = p >= 90 ? "crit" : p >= 75 ? "warn" : "ok";
  const fill = el("span", { className: `meter__fill meter__fill--${tone}` });
  fill.style.width = `${p}%`;
  return el("span", { className: "meter" }, [fill]);
}

function mountSystemPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, ["Нагрузка системы"]);
  const refreshBtn = el("button", { type: "button", className: "btn btn--ghost btn--sm", title: "Обновить" }, [icon("refresh")]);
  head.append(refreshBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const content = el("div", { className: "sys-content" }, ["Загрузка…"]);
  body.append(content);
  panel.append(body);

  let busy = false;
  let timer: number | null = null;

  const paint = (d: SystemOut) => {
    content.replaceChildren();
    if (!d.available || !d.host) {
      content.append(
        el("div", { className: "sys-empty" }, [
          "Метрики недоступны. Подними стек наблюдаемости: ",
          el("code", {}, ["docker compose … -f docker-compose.observability.yml up -d"]),
          ...(d.reason ? [el("div", { className: "sys-empty__reason" }, [d.reason])] : []),
        ]),
      );
      return;
    }
    const h = d.host;
    const grid = el("div", { className: "sys-grid" });
    grid.append(
      sysStat(
        "CPU",
        fmtPercentRaw(h.cpu_pct),
        h.cpu_cores ? `${h.cpu_cores} ядер` : undefined,
      ),
      sysStat(
        "Load avg",
        `${(h.load1 ?? 0).toFixed(2)}`,
        `5м ${(h.load5 ?? 0).toFixed(2)} · 15м ${(h.load15 ?? 0).toFixed(2)}`,
      ),
      sysStat(
        "RAM",
        fmtPercentRaw(h.mem_pct),
        `${fmtBytes(h.mem_used)} / ${fmtBytes(h.mem_total)}`,
      ),
      sysStat("Диск I/O", `↓ ${fmtRate(h.disk_read_bps)}`, `↑ ${fmtRate(h.disk_write_bps)}`),
    );
    content.append(grid);

    if (h.filesystems.length > 0) {
      content.append(el("div", { className: "sys-subhead" }, ["Файловые системы"]));
      const fsWrap = el("div", { className: "sys-fs" });
      for (const fs of h.filesystems) {
        fsWrap.append(
          el("div", { className: "sys-fs__row" }, [
            el("span", { className: "sys-fs__mount" }, [fs.mount]),
            meterBar(fs.pct),
            el("span", { className: "sys-fs__num" }, [
              `${fmtBytes(fs.used)} / ${fmtBytes(fs.total)} (${fs.pct ?? 0}%)`,
            ]),
          ]),
        );
      }
      content.append(fsWrap);
    }

    if (d.containers && d.containers.length > 0) {
      content.append(el("div", { className: "sys-subhead" }, ["Контейнеры"]));
      const table = el("table", { className: "sys-table" });
      table.append(
        el("thead", {}, [
          el("tr", {}, [
            el("th", {}, ["Контейнер"]),
            el("th", {}, ["CPU"]),
            el("th", {}, ["RAM"]),
            el("th", {}, ["I/O ↓"]),
            el("th", {}, ["I/O ↑"]),
          ]),
        ]),
      );
      const tbody = el("tbody");
      for (const c of d.containers) {
        tbody.append(
          el("tr", {}, [
            el("td", {}, [c.name]),
            el("td", {}, [c.cpu_pct != null ? `${c.cpu_pct}%` : "—"]),
            el("td", {}, [fmtBytes(c.mem_bytes)]),
            el("td", {}, [fmtRate(c.io_read_bps)]),
            el("td", {}, [fmtRate(c.io_write_bps)]),
          ]),
        );
      }
      table.append(tbody);
      content.append(table);
    }
  };

  const load = async () => {
    if (busy) return;
    busy = true;
    try {
      const data = await fetchJson<SystemOut>("/system");
      if (parseRoute().view !== "settings") return;
      paint(data);
    } catch (e) {
      content.replaceChildren(el("div", { className: "sys-empty" }, [e instanceof Error ? e.message : String(e)]));
    } finally {
      busy = false;
      if (timer !== null) clearTimeout(timer);
      if (parseRoute().view === "settings" && !document.hidden) {
        timer = window.setTimeout(() => void load(), 10000);
      }
    }
  };
  refreshBtn.addEventListener("click", () => void load());
  void load();
  return panel;
}

function fmtPercentRaw(v: number | null | undefined): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  return `${v.toFixed(1)}%`;
}

function mountHealthPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, ["Состояние сервисов"]);
  const refreshBtn = el(
    "button",
    { type: "button", className: "btn btn--ghost btn--sm", title: "Обновить" },
    [icon("refresh")],
  );
  head.append(refreshBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const banner = el("div", { className: "health-banner health-banner--loading" });
  const meta = el("div", { className: "health-meta" }, ["Проверка…"]);
  const grid = el("div", { className: "health-grid" });
  body.append(banner, grid, meta);
  panel.append(body);

  let busy = false;

  const paint = (data: HealthFull) => {
    banner.className = `health-banner health-banner--${data.status}`;
    banner.replaceChildren(
      el("span", { className: `health-dot health-dot--${data.status}` }),
      el("span", { className: "health-banner__text" }, [HEALTH_OVERALL_LABEL[data.status]]),
      el("span", { className: "health-banner__count" }, [
        `${data.summary.engines_ok}/${data.summary.engines_total} движков онлайн`,
      ]),
    );
    const core = data.components.filter((c) => c.kind === "core");
    const engines = data.components.filter((c) => c.kind === "engine");
    grid.replaceChildren();
    grid.append(el("div", { className: "health-grid__label" }, ["Ядро"]));
    const coreRow = el("div", { className: "health-cards" });
    for (const c of core) coreRow.append(healthCard(c));
    grid.append(coreRow);
    if (engines.length > 0) {
      grid.append(el("div", { className: "health-grid__label" }, ["Движки"]));
      const engRow = el("div", { className: "health-cards" });
      for (const c of engines) engRow.append(healthCard(c));
      grid.append(engRow);
    }
    meta.replaceChildren(
      el("span", { className: "live-dot" }),
      document.createTextNode(`Обновлено ${formatTime(new Date())}`),
    );
  };

  const tick = async (manual = false) => {
    if (busy) return;
    busy = true;
    if (manual) refreshBtn.classList.add("is-spinning");
    try {
      const data = await fetchJson<HealthFull>("/health/full");
      if (parseRoute().view !== "settings") return;
      paint(data);
    } catch (e) {
      banner.className = "health-banner health-banner--down";
      banner.replaceChildren(
        el("span", { className: "health-dot health-dot--down" }),
        el("span", { className: "health-banner__text" }, ["Не удалось получить статус"]),
      );
      meta.textContent = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
      refreshBtn.classList.remove("is-spinning");
      if (settingsHealthTimer !== null) clearTimeout(settingsHealthTimer);
      if (parseRoute().view === "settings" && !document.hidden) {
        settingsHealthTimer = setTimeout(() => void tick(), 5000);
      }
    }
  };

  refreshBtn.addEventListener("click", () => void tick(true));
  void tick();
  return panel;
}

const ROLE_LABEL: Record<Role, string> = {
  viewer: "Наблюдатель (только чтение)",
  operator: "Оператор (управление раздачами)",
  admin: "Администратор (полный доступ)",
};

async function loadMe(): Promise<void> {
  try {
    const me = await fetchJson<MeOut>("/auth/me");
    currentMe = me;
    currentRole = me.role;
  } catch {
    currentMe = null;
    currentRole = null;
  }
}

async function loginWithPassword(username: string, password: string): Promise<void> {
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let msg = "неверный логин или пароль";
    try {
      const b = (await res.json()) as { error?: { message?: string } };
      if (b.error?.message) msg = b.error.message;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  const data = (await res.json()) as { token: string };
  setApiKey(data.token);
}

function showLoginDialog(): void {
  document.querySelector(".modal-overlay.login-overlay")?.remove();
  const overlay = el("div", { className: "modal-overlay login-overlay" });
  const dialog = el("div", { className: "modal-dialog", role: "dialog", "aria-modal": "true" });

  const userInput = el("input", {
    type: "text",
    placeholder: "имя пользователя",
    className: "login-input",
    autocomplete: "username",
  }) as HTMLInputElement;
  const passInput = el("input", {
    type: "password",
    placeholder: "пароль",
    className: "login-input",
    autocomplete: "current-password",
  }) as HTMLInputElement;

  const keyInput = el("input", {
    type: "password",
    placeholder: "sk_…",
    className: "login-input login-input--mono",
    value: getApiKey(),
  }) as HTMLInputElement;
  const keyWrap = el("div", { className: "login-keywrap", hidden: "" }, [field("API-ключ", keyInput)]);
  const toggleKey = el("button", { type: "button", className: "btn btn--ghost login-alt" }, [
    "Войти по API-ключу",
  ]);
  toggleKey.addEventListener("click", () => {
    const show = keyWrap.hasAttribute("hidden");
    if (show) keyWrap.removeAttribute("hidden");
    else keyWrap.setAttribute("hidden", "");
    toggleKey.textContent = show ? "Скрыть API-ключ" : "Войти по API-ключу";
    if (show) keyInput.focus();
  });

  const errLine = el("p", { className: "login-error", hidden: "" });
  const submit = el("button", { type: "button", className: "btn btn--primary login-submit" }, ["Войти"]);

  const finish = async () => {
    await loadMe();
    if (currentRole) {
      overlay.remove();
      render();
      return true;
    }
    return false;
  };

  const doLogin = async () => {
    submit.disabled = true;
    errLine.hidden = true;
    try {
      const keyVisible = !keyWrap.hasAttribute("hidden");
      const u = userInput.value.trim();
      const p = passInput.value;
      if (keyVisible && keyInput.value.trim() && !u) {
        setApiKey(keyInput.value.trim());
        // Явно отмечаем вход по ключу в аудите (best-effort).
        try {
          await fetchJson("/auth/key-login", { method: "POST" });
        } catch {
          /* невалидный ключ — обработается ниже через finish() */
        }
        if (await finish()) return;
        throw new Error("Неверный ключ или нет доступа");
      }
      if (!u || !p) throw new Error("Введите логин и пароль");
      await loginWithPassword(u, p);
      if (await finish()) return;
      throw new Error("Не удалось войти");
    } catch (e) {
      errLine.textContent = e instanceof Error ? e.message : String(e);
      errLine.hidden = false;
      submit.disabled = false;
    }
  };
  submit.addEventListener("click", () => void doLogin());
  for (const inp of [userInput, passInput, keyInput]) {
    inp.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") void doLogin();
    });
  }

  dialog.classList.add("login-dialog");
  const divider = el("div", { className: "login-divider" }, ["или"]);
  dialog.append(
    el("div", { className: "login-head" }, [
      el("h2", { className: "modal-title" }, ["Вход"]),
      el("p", { className: "login-sub" }, ["Войдите по имени пользователя и паролю."]),
    ]),
    el("div", { className: "login-form" }, [
      field("Пользователь", userInput),
      field("Пароль", passInput),
      keyWrap,
      errLine,
      submit,
    ]),
    divider,
    toggleKey,
  );
  overlay.append(dialog);
  document.body.append(overlay);
  userInput.focus();
}

function mountAccountPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["Аккаунт"]));
  const body = el("div", { className: "panel__body" });
  const role = currentRole ?? "viewer";
  const who =
    currentMe && currentMe.source === "session"
      ? `${currentMe.name} · ${ROLE_LABEL[role]}`
      : currentMe
        ? ROLE_LABEL[role]
        : "Не авторизован";
  body.append(
    el("div", { className: "account-row" }, [
      el("span", { className: "health-dot health-dot--ok" }),
      el("span", {}, [who]),
    ]),
  );
  if (currentMe?.source === "anonymous") {
    body.append(
      el("p", { className: "field__hint" }, [
        "Учётных данных ещё нет — доступ открыт. Создайте пользователя или admin-ключ ниже, чтобы включить защиту.",
      ]),
    );
  }
  const btnRow = el("div", { className: "btn-row" });
  const logoutBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Выйти"]);
  logoutBtn.addEventListener("click", async () => {
    try {
      await fetchJson("/auth/logout", { method: "POST" });
    } catch {
      /* ignore */
    }
    setApiKey("");
    currentMe = null;
    currentRole = null;
    showLoginDialog();
  });
  const changeBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Сменить аккаунт"]);
  changeBtn.addEventListener("click", () => showLoginDialog());
  btnRow.append(logoutBtn, changeBtn);
  body.append(btnRow);
  panel.append(body);
  return panel;
}

function mountApiKeysPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["API-ключи и доступ"]));
  const body = el("div", { className: "panel__body" });

  const nameInput = el("input", { type: "text", placeholder: "Название (напр. «ноутбук»)" }) as HTMLInputElement;
  const roleSelect = el("select", { className: "select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["admin", "admin — полный доступ"],
    ["operator", "operator — управление раздачами"],
    ["viewer", "viewer — только чтение"],
  ]) {
    roleSelect.append(el("option", { value: val }, [label]));
  }
  const createBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Создать ключ"]);
  const list = el("div", { className: "keys-list" });

  const reload = async () => {
    try {
      const keys = await fetchJson<
        {
          id: number;
          name: string;
          prefix: string;
          role: Role;
          enabled: boolean;
          created_at: string | null;
          last_used_at: string | null;
        }[]
      >("/auth/keys");
      list.replaceChildren();
      if (keys.length === 0) {
        list.append(el("p", { className: "field__hint" }, ["Ключей пока нет"]));
        return;
      }
      for (const k of keys) {
        const row = el("div", { className: `key-row${k.enabled ? "" : " key-row--off"}` });
        const meta = el("div", { className: "key-row__meta" }, [
          el("span", { className: "key-row__name" }, [k.name || "(без названия)"]),
          el("span", { className: "key-row__sub" }, [
            `${k.prefix}… · ${k.role}${k.enabled ? "" : " · выключен"}${
              k.last_used_at ? ` · использован ${new Date(k.last_used_at).toLocaleDateString("ru-RU")}` : ""
            }`,
          ]),
        ]);
        const toggle = el("button", { type: "button", className: "btn btn--sm" }, [
          k.enabled ? "Выключить" : "Включить",
        ]);
        toggle.addEventListener("click", async () => {
          try {
            await fetchJson(`/auth/keys/${k.id}`, {
              method: "PATCH",
              body: JSON.stringify({ enabled: !k.enabled }),
            });
            await reload();
          } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), true);
          }
        });
        const del = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["Удалить"]);
        del.addEventListener("click", async () => {
          if (!window.confirm(`Удалить ключ «${k.name || k.prefix}»?`)) return;
          try {
            await fetchDelete(`/auth/keys/${k.id}`);
            await reload();
          } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), true);
          }
        });
        row.append(meta, el("div", { className: "btn-row" }, [toggle, del]));
        list.append(row);
      }
    } catch (e) {
      list.replaceChildren(el("p", { className: "field__hint" }, [e instanceof Error ? e.message : String(e)]));
    }
  };

  createBtn.addEventListener("click", async () => {
    createBtn.disabled = true;
    try {
      const res = await fetchJson<{ key: string; item: { prefix: string } }>("/auth/keys", {
        method: "POST",
        body: JSON.stringify({ name: nameInput.value.trim(), role: roleSelect.value }),
      });
      nameInput.value = "";
      showNewKeyDialog(res.key);
      await reload();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      createBtn.disabled = false;
    }
  });

  body.append(
    el("p", { className: "field__hint" }, ["Ключ показывается один раз при создании — сохраните его."]),
    el("div", { className: "keys-form" }, [nameInput, roleSelect, createBtn]),
    list,
  );
  panel.append(body);
  void reload();
  return panel;
}

type UserItem = {
  id: number;
  username: string;
  role: Role;
  enabled: boolean;
  protected?: boolean;
  created_at: string | null;
  last_login_at: string | null;
};

function mountUsersPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["Пользователи"]));
  const body = el("div", { className: "panel__body" });

  const nameInput = el("input", { type: "text", placeholder: "имя пользователя" }) as HTMLInputElement;
  const passInput = el("input", { type: "password", placeholder: "пароль (мин. 6)" }) as HTMLInputElement;
  const roleSelect = el("select", { className: "select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["admin", "admin — полный доступ"],
    ["operator", "operator — управление раздачами"],
    ["viewer", "viewer — только чтение"],
  ]) {
    roleSelect.append(el("option", { value: val }, [label]));
  }
  roleSelect.value = "operator";
  const createBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Добавить"]);
  const list = el("div", { className: "keys-list" });

  const reload = async () => {
    try {
      const users = await fetchJson<UserItem[]>("/auth/users");
      list.replaceChildren();
      if (users.length === 0) {
        list.append(el("p", { className: "field__hint" }, ["Пользователей пока нет"]));
        return;
      }
      for (const u of users) {
        const row = el("div", { className: `key-row${u.enabled ? "" : " key-row--off"}` });
        const meta = el("div", { className: "key-row__meta" }, [
          el("span", { className: "key-row__name" }, [
            u.username,
            u.protected ? el("span", { className: "key-row__tag" }, ["основной"]) : "",
          ]),
          el("span", { className: "key-row__sub" }, [
            `${u.role}${u.enabled ? "" : " · выключен"}${
              u.last_login_at ? ` · вход ${new Date(u.last_login_at).toLocaleDateString("ru-RU")}` : ""
            }`,
          ]),
        ]);
        const roleControl: HTMLElement = u.protected
          ? el("span", { className: "role-static" }, ["admin"])
          : (() => {
              const roleSel = el("select", { className: "select select--sm" }) as HTMLSelectElement;
              for (const r of ["admin", "operator", "viewer"]) roleSel.append(el("option", { value: r }, [r]));
              roleSel.value = u.role;
              roleSel.addEventListener("change", async () => {
                try {
                  await fetchJson(`/auth/users/${u.id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ role: roleSel.value }),
                  });
                  await reload();
                } catch (e) {
                  showToast(e instanceof Error ? e.message : String(e), true);
                }
              });
              return roleSel;
            })();
        const pwBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Пароль"]);
        pwBtn.addEventListener("click", async () => {
          const np = window.prompt(`Новый пароль для «${u.username}» (мин. 6):`);
          if (!np) return;
          try {
            await fetchJson(`/auth/users/${u.id}`, {
              method: "PATCH",
              body: JSON.stringify({ password: np }),
            });
            showToast("Пароль обновлён");
          } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), true);
          }
        });
        const toggle = el("button", { type: "button", className: "btn btn--sm" }, [
          u.enabled ? "Выключить" : "Включить",
        ]);
        toggle.addEventListener("click", async () => {
          try {
            await fetchJson(`/auth/users/${u.id}`, {
              method: "PATCH",
              body: JSON.stringify({ enabled: !u.enabled }),
            });
            await reload();
          } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), true);
          }
        });
        const del = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["Удалить"]);
        del.addEventListener("click", async () => {
          if (!window.confirm(`Удалить пользователя «${u.username}»?`)) return;
          try {
            await fetchDelete(`/auth/users/${u.id}`);
            await reload();
          } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), true);
          }
        });
        const actions = u.protected ? [roleControl, pwBtn] : [roleControl, pwBtn, toggle, del];
        row.append(meta, el("div", { className: "btn-row" }, actions));
        list.append(row);
      }
    } catch (e) {
      list.replaceChildren(el("p", { className: "field__hint" }, [e instanceof Error ? e.message : String(e)]));
    }
  };

  createBtn.addEventListener("click", async () => {
    const u = nameInput.value.trim();
    const p = passInput.value;
    if (!u || p.length < 6) {
      showToast("Имя и пароль (мин. 6) обязательны", true);
      return;
    }
    createBtn.disabled = true;
    try {
      await fetchJson("/auth/users", {
        method: "POST",
        body: JSON.stringify({ username: u, password: p, role: roleSelect.value }),
      });
      nameInput.value = "";
      passInput.value = "";
      showToast("Пользователь добавлен");
      await reload();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      createBtn.disabled = false;
    }
  });

  body.append(
    el("p", { className: "field__hint" }, ["Вход по имени и паролю. Роль можно менять в списке."]),
    el("div", { className: "keys-form" }, [nameInput, passInput, roleSelect, createBtn]),
    list,
  );
  panel.append(body);
  void reload();
  return panel;
}

type BackupItem = { filename: string; size: number; created_at: string };
type BackupsOut = { dir: string; available: boolean; items: BackupItem[] };

function mountBackupsPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, ["Резервные копии БД"]);
  const createBtn = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Создать сейчас"]);
  head.append(createBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const hint = el("p", { className: "field__hint" }, ["Загрузка…"]);
  const list = el("div", { className: "keys-list" });
  body.append(hint, list);
  panel.append(body);

  const reload = async () => {
    try {
      const data = await fetchJson<BackupsOut>("/backups");
      if (!data.available) {
        hint.textContent = `Каталог бэкапов недоступен (${data.dir}). Проверьте монтирование.`;
        list.replaceChildren();
        return;
      }
      hint.textContent = `Каталог: ${data.dir} · копий: ${data.items.length}`;
      list.replaceChildren();
      if (data.items.length === 0) {
        list.append(el("p", { className: "field__hint" }, ["Пока нет ни одной копии"]));
        return;
      }
      for (const b of data.items) {
        const row = el("div", { className: "key-row" });
        const when = new Date(b.created_at).toLocaleString("ru-RU");
        row.append(
          el("div", { className: "key-row__meta" }, [
            el("span", { className: "key-row__name" }, [b.filename]),
            el("span", { className: "key-row__sub" }, [`${when} · ${fmtBytes(b.size)}`]),
          ]),
        );
        const restoreBtn = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["Восстановить"]);
        restoreBtn.addEventListener("click", () => void doRestore(b, restoreBtn));
        row.append(el("div", { className: "btn-row" }, [restoreBtn]));
        list.append(row);
      }
    } catch (e) {
      hint.textContent = e instanceof Error ? e.message : String(e);
    }
  };

  const doRestore = async (b: BackupItem, btn: HTMLButtonElement) => {
    if (
      !window.confirm(
        `Восстановить БД из «${b.filename}»?\n\nТекущие данные будут перезаписаны этим снимком. ` +
          "Действие необратимо.",
      )
    )
      return;
    btn.disabled = true;
    try {
      await fetchJson("/backups/restore", {
        method: "POST",
        body: JSON.stringify({ filename: b.filename }),
      });
      showToast("БД восстановлена из копии");
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      btn.disabled = false;
    }
  };

  createBtn.addEventListener("click", async () => {
    createBtn.disabled = true;
    try {
      const res = await fetchJson<{ filename: string; size: number }>("/backups", { method: "POST" });
      showToast(`Копия создана: ${res.filename}`);
      await reload();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      createBtn.disabled = false;
    }
  });

  void reload();
  return panel;
}

type AuditItem = {
  id: number;
  created_at: string;
  actor: string;
  role: string;
  method: string;
  path: string;
  status: number;
  ip: string;
  summary: string;
};

function auditStatusClass(status: number): string {
  if (status >= 400) return "audit-status--err";
  if (status >= 300) return "audit-status--warn";
  return "audit-status--ok";
}

function mountAuditPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, ["Журнал действий"]);
  const refreshBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Обновить"]);
  head.append(refreshBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const hint = el("p", { className: "field__hint" }, ["Загрузка…"]);
  const list = el("div", { className: "audit-list" });
  body.append(hint, list);
  panel.append(body);

  const reload = async () => {
    try {
      const rows = await fetchJson<AuditItem[]>("/audit?limit=200");
      hint.textContent = `Последние действия · записей: ${rows.length}`;
      list.replaceChildren();
      if (rows.length === 0) {
        list.append(el("p", { className: "field__hint" }, ["Журнал пуст"]));
        return;
      }
      for (const r of rows) {
        const when = new Date(r.created_at).toLocaleString("ru-RU");
        const row = el("div", { className: "audit-row" }, [
          el("span", { className: `audit-status ${auditStatusClass(r.status)}` }, [String(r.status)]),
          el("div", { className: "audit-row__meta" }, [
            el("span", { className: "audit-row__summary" }, [r.summary]),
            el("span", { className: "audit-row__sub" }, [
              `${r.actor || "—"}${r.role ? ` (${r.role})` : ""} · ${when}${r.ip ? ` · ${r.ip}` : ""}`,
            ]),
          ]),
        ]);
        list.append(row);
      }
    } catch (e) {
      hint.textContent = e instanceof Error ? e.message : String(e);
    }
  };

  refreshBtn.addEventListener("click", () => void reload());
  void reload();
  return panel;
}

function showNewKeyDialog(key: string): void {
  const overlay = el("div", { className: "modal-overlay" });
  const dialog = el("div", { className: "modal-dialog", role: "dialog", "aria-modal": "true" });
  const keyBox = el("pre", { className: "def-list__magnet new-key-box" });
  keyBox.textContent = key;
  const copyBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Скопировать"]);
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(key);
      showToast("Скопировано");
    } catch {
      showToast("Не удалось скопировать", true);
    }
  });
  const closeBtn = el("button", { type: "button", className: "btn btn--primary" }, ["Готово"]);
  closeBtn.addEventListener("click", () => overlay.remove());
  dialog.append(
    el("h2", { className: "modal-title" }, ["Новый ключ создан"]),
    el("p", { className: "modal-text" }, ["Сохраните ключ сейчас — позже он не отобразится."]),
    keyBox,
    el("div", { className: "modal-actions" }, [copyBtn, closeBtn]),
  );
  overlay.append(dialog);
  document.body.append(overlay);
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
  const themeSelect = el("select", { className: "select select--inline" }) as HTMLSelectElement;
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

  const globalLimits = canWrite() ? mountGlobalLimitsPanel() : null;
  if (globalLimits) globalLimits.setAttribute("open", "");

  const tabDefs: { id: SettingsTab; label: string; visible: boolean; panels: () => HTMLElement[] }[] = [
    {
      id: "info",
      label: "Информация",
      visible: true,
      panels: () => [statsHost, mountAlertsPanel(), mountSystemPanel(), mountHealthPanel(), themePanel],
    },
    {
      id: "users",
      label: "Пользователи",
      visible: true,
      panels: () => {
        const out = [mountAccountPanel()];
        if (isAdmin()) out.push(mountUsersPanel(), mountApiKeysPanel());
        return out;
      },
    },
    {
      id: "limits",
      label: "Лимиты",
      visible: canWrite(),
      panels: () => {
        const out: HTMLElement[] = [];
        if (globalLimits) out.push(globalLimits);
        out.push(mountEngineLimitsPanel(), mountQuotasPanel(), mountNetSettingsPanel(), mountPrivateMaintenancePanel());
        return out;
      },
    },
    {
      id: "logs",
      label: "Логи",
      visible: isAdmin(),
      panels: () => [mountAuditPanel(), mountBackupsPanel()],
    },
  ];

  const visibleTabs = tabDefs.filter((t) => t.visible);
  if (!visibleTabs.some((t) => t.id === activeSettingsTab)) {
    activeSettingsTab = visibleTabs[0]?.id ?? "info";
  }

  const tabBar = el("div", { className: "settings-tabs", role: "tablist" });
  const panes: Partial<Record<SettingsTab, HTMLElement>> = {};
  const buttons: Partial<Record<SettingsTab, HTMLButtonElement>> = {};

  const selectTab = (id: SettingsTab) => {
    activeSettingsTab = id;
    for (const t of visibleTabs) {
      const on = t.id === id;
      const pane = panes[t.id];
      const btn = buttons[t.id];
      if (pane) pane.hidden = !on;
      if (btn) {
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-selected", on ? "true" : "false");
      }
    }
  };

  const paneWrap = el("div", { className: "settings-panes" });
  for (const t of visibleTabs) {
    const btn = el("button", { type: "button", className: "settings-tab", role: "tab" }, [t.label]) as HTMLButtonElement;
    btn.addEventListener("click", () => selectTab(t.id));
    buttons[t.id] = btn;
    tabBar.append(btn);
    const pane = el("div", { className: "settings-pane" }, t.panels());
    panes[t.id] = pane;
    paneWrap.append(pane);
  }

  root.append(back, header, tabBar, paneWrap);
  selectTab(activeSettingsTab);

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

async function bootstrap(): Promise<void> {
  await loadMe();
  if (!currentRole) {
    showLoginDialog();
    return;
  }
  render();
}

document.title = "Раздача";
applyTheme(getThemeMode());
window.addEventListener("hashchange", () => render());
void bootstrap();
