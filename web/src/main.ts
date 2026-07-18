import "./style.css";
import { WEB_VERSION } from "./version";
import { onWsUnavailable, wsAvailable, wsSubscribe } from "./ws";

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

type CreatorBrowseItem = { name: string; path: string; is_dir: boolean; size: number; modified: number };
type CreatorTaskOut = {
  engine_id: string;
  id: number;
  source_path: string;
  save_path: string;
  status: string;
  progress: number;
  message: string;
  error: string | null;
  name: string;
  file_count: number;
  created_at: number;
  updated_at: number;
  has_torrent: boolean;
};
type CreateMode = "seed" | "download";

type EngineRegistryItem = {
  id: string;
  url: string;
  storage_prefix: string;
  media_path?: string | null;
  listen_port: number | null;
  enabled: boolean;
  last_seen: string | null;
  age_seconds: number | null;
  stale: boolean;
  in_pool: boolean;
  source: string;
};

type ComponentItem = {
  service: string;
  container: string;
  state: string | null;
  status: string | null;
};

type ComponentsOut = {
  available: boolean;
  reason?: string;
  components?: ComponentItem[];
};

type ConnectivityOut = {
  id: string;
  url: string;
  tls: boolean;
  reachable: boolean;
  api_latency_ms: number | null;
  bt_listening?: boolean;
  bt_reachable_hint?: boolean | null;
  bt_port?: number | null;
  error?: string | null;
};

type EngineInfoOut = {
  id: string;
  registry: {
    url: string;
    advertise_host: string;
    tls: boolean;
    storage_prefix: string;
    media_path?: string | null;
    listen_port: number | null;
    enabled: boolean;
    in_pool: boolean;
    source: string;
    last_seen: string | null;
    age_seconds: number | null;
    stale: boolean;
  };
  connectivity: {
    reachable: boolean;
    api_latency_ms: number | null;
    bt_listening?: boolean;
    bt_reachable_hint?: boolean | null;
    bt_port?: number | null;
    bt?: { dht_nodes?: number | null; peers?: number | null } | null;
    error?: string;
  } | null;
  session: {
    torrents?: number;
    torrents_active?: number;
    download_rate?: number;
    upload_rate?: number;
    total_uploaded?: number;
    total_downloaded?: number;
    peers?: number;
    seeds?: number;
  } | null;
  sysinfo: {
    hostname?: string;
    backend?: string | null;
    os?: string;
    python?: string;
    libtorrent?: string | null;
    version?: string | null;
    built_at?: string | null;
    uptime_seconds?: number;
    data_root?: string;
    storage_path?: string | null;
    local_ip?: string | null;
    wan_ip?: string | null;
    advertise_url?: string;
    listen_interfaces?: string;
    cpu_count?: number | null;
    cpu_pct?: number | null;
    load1?: number | null;
    load5?: number | null;
    load15?: number | null;
    mem_total?: number | null;
    mem_available?: number | null;
    proc_rss?: number | null;
    disk_total?: number | null;
    disk_free?: number | null;
  } | null;
  errors?: Record<string, string>;
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
  version?: string | null;
  built_at?: string | null;
  meta?: Record<string, unknown> | null;
};
type HealthFull = {
  status: HealthStatus;
  generated_at: string;
  summary: { engines_ok: number; engines_total: number };
  components: HealthComponent[];
};

type TorrentDetailOut = TorrentOut & { runtime: RuntimeOut | null; peer_list?: TorrentPeerOut[] };
type TorrentPageOut = { items: TorrentOut[]; total: number; limit: number; offset: number };
type UpdateMatchItem = { filename: string; candidates: TorrentOut[] };
type UpdateMatchResult = { items: UpdateMatchItem[] };
type Route = { view: "list" } | { view: "detail"; id: number } | { view: "settings" };
type DeleteTorrentChoice = "cancel" | "torrent_only" | "torrent_and_files";

let listPollTimer: ReturnType<typeof setTimeout> | null = null;
let listStream: EventSource | null = null;
let listStatsUnsub: (() => void) | null = null;
let listUnavailOff: (() => void) | null = null;
let detailPollTimer: ReturnType<typeof setTimeout> | null = null;
let settingsHealthTimer: ReturnType<typeof setTimeout> | null = null;
let settingsEnginesUnsub: (() => void) | null = null;
let listAbort: AbortController | null = null;
let detailAbort: AbortController | null = null;
let detailWsOff: (() => void) | null = null;
let listLoadGeneration = 0;
let lastListItems: TorrentOut[] = [];
const PAGE_SIZES = [20, 50, 100] as const;
let listPage = 0;
let listPageSize: number = ((): number => {
  const v = parseInt(lsGet("ui.pageSize") || "50", 10);
  return (PAGE_SIZES as readonly number[]).includes(v) ? v : 50;
})();
let listTotal = 0;
let pagerHost: HTMLElement | null = null;
let listReload: (() => void) | null = null;
let toastTimer: ReturnType<typeof setTimeout> | null = null;
let selectedIds = new Set<number>();
let selectionChanged: (() => void) | null = null;

type Role = "viewer" | "operator" | "admin";
type MeOut = { name: string; role: Role; source: string };
let currentRole: Role | null = null;
let currentMe: MeOut | null = null;

type SettingsTab = "info" | "users" | "limits" | "maint" | "logs";
const SETTINGS_TABS: readonly SettingsTab[] = ["info", "users", "limits", "maint", "logs"];
const SETTINGS_TAB_KEY = "seedingSettingsTab";

function readActiveSettingsTab(): SettingsTab {
  try {
    const v = localStorage.getItem(SETTINGS_TAB_KEY);
    if (v && (SETTINGS_TABS as readonly string[]).includes(v)) return v as SettingsTab;
  } catch {
    /* ignore */
  }
  return "info";
}

let activeSettingsTab: SettingsTab = readActiveSettingsTab();

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

const SORT_VALUES = [
  "added",
  "name",
  "up",
  "down",
  "peers",
  "uploaded",
  "ratio",
  "size",
  "progress",
] as const;
type ListSort = (typeof SORT_VALUES)[number];
const STATE_VALUES = ["", "active", "peers", "idle", "incomplete", "error"] as const;
type ListState = (typeof STATE_VALUES)[number];
type ListDensity = "comfortable" | "compact";
type ListView = "cards" | "table";
type ThemeMode = "auto" | "light" | "dark";

let listSearch = lsGet("ui.search") ?? "";
let listStatusFilter = lsGet("ui.status") ?? "";
let listLabelFilter = lsGet("ui.label") ?? "";
let listEngineFilter = lsGet("ui.engine") ?? "";
let listState: ListState = ((): ListState => {
  const v = lsGet("ui.state") ?? "";
  return (STATE_VALUES as readonly string[]).includes(v) ? (v as ListState) : "";
})();
let listSort: ListSort = ((): ListSort => {
  const v = lsGet("ui.sort") ?? "";
  return (SORT_VALUES as readonly string[]).includes(v) ? (v as ListSort) : "name";
})();
let listDensity: ListDensity = lsGet("ui.density") === "compact" ? "compact" : "comfortable";
let listView: ListView = lsGet("ui.view") === "table" ? "table" : "cards";

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
  if (listStatsUnsub !== null) {
    listStatsUnsub();
    listStatsUnsub = null;
  }
  if (listUnavailOff !== null) {
    listUnavailOff();
    listUnavailOff = null;
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
  settingsEnginesUnsub?.();
  settingsEnginesUnsub = null;
  listAbort?.abort();
  listAbort = null;
  detailAbort?.abort();
  detailAbort = null;
  detailWsOff?.();
  detailWsOff = null;
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
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>',
  filter: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
  rows: '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
  grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
  table:
    '<rect x="3" y="3" width="18" height="18" rx="1"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/>',
  upload:
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  "file-plus":
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/>',
  list:
    '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
  download:
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="3" x2="12" y2="15"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  trash:
    '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
  play: '<polygon points="5 3 19 12 5 21 5 3"/>',
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

/** Знак-логотип в шапке: граф раздачи (центральный узел раздаёт пирам). */
function brandMark(): HTMLElement {
  const span = el("span", { className: "brand__mark", "aria-hidden": "true" });
  span.innerHTML =
    '<svg viewBox="0 0 32 32" width="34" height="34" xmlns="http://www.w3.org/2000/svg">' +
    '<rect width="32" height="32" rx="8" fill="var(--accent)"/>' +
    '<g stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none">' +
    '<path d="M16 11.2 9.6 19.4M16 11.2l6.4 8.2M11.4 22h9.2"/>' +
    '</g>' +
    '<g fill="#fff">' +
    '<circle cx="16" cy="9" r="2.6"/>' +
    '<circle cx="9" cy="22" r="2.6"/>' +
    '<circle cx="23" cy="22" r="2.6"/>' +
    '</g></svg>';
  return span;
}

/** Лого-локап (знак + название + подпись) для шапки. */
function brandLockup(): HTMLElement {
  return el("div", { className: "brand" }, [
    brandMark(),
    el("div", { className: "brand__text" }, [
      el("h1", { className: "brand__name" }, ["Раздача"]),
      el("p", { className: "brand__tag" }, ["Управление торрентами"]),
    ]),
  ]);
}

/** Подвал с копирайтом разработчика (общий для всех экранов). */
function appFooter(): HTMLElement {
  const link = el("a", {
    className: "app-footer__link",
    href: "https://hw-s.ru",
    target: "_blank",
    rel: "noopener noreferrer",
  }, ["HW-S.ru"]);
  return el("footer", { className: "app-footer" }, [
    document.createTextNode("Разработано: "),
    link,
    document.createTextNode(" by Hardkor"),
    el("span", { className: "app-footer__ver" }, [` · v${WEB_VERSION}`]),
  ]);
}

// Роутинг через History API (чистые пути, без "#"). nginx отдаёт index.html на любой путь
// (try_files … /index.html), поэтому /torrent/1 и /settings работают и при перезагрузке.
function parseRoute(): Route {
  const path = window.location.pathname;
  const m = /^\/torrent\/(\d+)\/?$/.exec(path);
  if (m) return { view: "detail", id: Number(m[1]) };
  if (path === "/settings" || path === "/settings/") return { view: "settings" };
  return { view: "list" };
}

function pushPath(path: string): void {
  if (window.location.pathname !== path) {
    window.history.pushState(null, "", path);
  }
}

function setHashList(): void {
  pushPath("/");
}

function setHashDetail(id: number): void {
  pushPath(`/torrent/${id}`);
}

function setHashSettings(): void {
  pushPath("/settings");
}

function navLink(label: string, onClick: () => void): HTMLElement {
  const a = el("a", { href: "/", className: "back-link" }, [label]);
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

function fmtAddedCell(iso: string | null | undefined): Node {
  if (!iso) return document.createTextNode("—");
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return document.createTextNode("—");
  const short = d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  return el("span", { className: "ttable__date", title: d.toLocaleString("ru-RU") }, [short]);
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
  // При живом WS живые поля приходят пушем torrent:{id}; полную пересборку (пиры/файлы/трекеры)
  // оставляем редким бэкстопом — реже моргает и меньше нагрузка.
  if (wsAvailable()) return 10_000;
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
  // Флаг «перенос ещё живёт»: loadDetail не пересобирает деталь, пока он "1", иначе на фазе
  // finalizing (статус в БД уже seeding) панель перерисовалась бы и вместо завершения показала
  // селектор движка. Снимаем флаг только когда виджет реально закончил (active=false).
  wrap.dataset.active = "1";
  const label = el("div", { className: "migrate-progress__label" }, ["Подготовка…"]);
  const track = el("div", { className: "progress" });
  const fill = el("div", { className: "progress__bar" });
  track.append(fill);
  wrap.append(label, track);

  let timer = 0;
  let wsOff: (() => void) | null = null;
  let finished = false;
  let lastSpeed: number | null = null;  // грэйс: держим последнюю скорость при кратком провале до 0
  let lastWsMs = 0;  // время последнего WS-пуша: пока свежий — частый поллинг-бэкстоп отступает
  const stop = () => {
    if (timer) window.clearInterval(timer);
    timer = 0;
    if (wsOff) {
      wsOff();
      wsOff = null;
    }
  };
  // Отрисовка одного снимка прогресса (общая для WS-пуша и поллинга-страховки).
  const render = (s: MigrateStatusOut) => {
    const phase = s.phase || "migrating";
    const pct = typeof s.progress === "number" ? Math.round(s.progress * 100) : null;
    let text = MIGRATE_PHASE_LABELS[phase] ?? phase;
    const tname: Record<string, string> = { media: "общий /media", http: "через оркестратор", direct: "напрямую" };
    if (s.transport && phase !== "error" && phase !== "done") text += ` · ${tname[s.transport] ?? s.transport}`;
    if (pct != null && (phase === "copying" || phase === "checking")) text += ` · ${pct}%`;
    if (phase === "copying" && s.total) text += `  (${fmtBytes(s.copied)} / ${fmtBytes(s.total)})`;
    // Скорость: при копировании держим последнее значение, если в текущем снимке её нет (краткий
    // провал до 0 на стороне движка), чтобы строка не мигала; сбрасываем при смене фазы.
    if (phase === "copying") {
      const cur = typeof s.speed === "number" && s.speed > 0 ? s.speed : null;
      if (cur != null) lastSpeed = cur;
      const shown = cur ?? lastSpeed;
      if (shown) text += ` · ${fmtBytes(shown)}/с`;
    } else {
      lastSpeed = null;
    }
    if (phase === "copying" && s.eta) text += ` · ост. ${fmtEta(s.eta)}`;
    if (phase === "error" && s.message) text += ` — ${s.message}`;
    label.textContent = text;
    const indeterminate = pct == null && phase !== "error" && phase !== "done";
    wrap.classList.toggle("migrate-progress--indeterminate", indeterminate);
    wrap.classList.toggle("migrate-progress--error", phase === "error");
    fill.style.width = pct != null ? `${pct}%` : "100%";
    if (s.active === false && !finished) {
      finished = true;
      wrap.dataset.active = "0";  // разрешаем loadDetail пересобрать деталь после завершения
      stop();
      window.setTimeout(() => onDone(), 1000);
    }
  };
  const tick = async () => {
    if (!document.body.contains(wrap)) {
      stop();
      return;
    }
    // Пропускаем тик, если только что пришёл WS-пуш — тогда WS остаётся основным источником,
    // а частый поллинг служит гарантией обновления (3×), если WS-канал молчит.
    if (Date.now() - lastWsMs < 800) return;
    try {
      render(await fetchJson<MigrateStatusOut>(`/torrents/${data.id}/migrate-status`));
    } catch {
      // транзиентная ошибка опроса — попробуем на следующем тике
    }
  };
  // WS (Фаза 7): мгновенные пуши прогресса. Поллинг ниже — частый бэкстоп (≈3×/с), который
  // сам отступает, пока WS свежий, и подхватывает, если пуши не доходят.
  wsOff = wsSubscribe(`migrate:${data.id}`, (msg) => {
    lastWsMs = Date.now();
    if (!document.body.contains(wrap)) {
      stop();
      return;
    }
    render(msg.data as MigrateStatusOut);
  });
  void tick();
  timer = window.setInterval(() => void tick(), 333);
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
        const a = el("a", { href: `/torrent/${t.id}`, title: fullName }, [shownName]);
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

type TableColumn = {
  key: string;
  label: string;
  sort?: ListSort;
  num?: boolean;
  cell: (t: TorrentOut) => Node | string;
};

function tableColumns(): TableColumn[] {
  return [
    {
      key: "name",
      label: "Имя",
      sort: "name",
      cell: (t) => {
        const fullName = t.display_name || `Торрент #${t.id}`;
        const shownName = fullName.replace(/\.torrent$/i, "") || fullName;
        const a = el("a", { href: `/torrent/${t.id}`, title: fullName }, [shownName]);
        a.addEventListener("click", (ev) => {
          ev.preventDefault();
          setHashDetail(t.id);
          window.dispatchEvent(new HashChangeEvent("hashchange"));
        });
        return a;
      },
    },
    {
      key: "status",
      label: "Статус",
      cell: (t) => el("span", { className: badgeClass(effectiveStatus(t)) }, [displayStatusLabel(t)]),
    },
    { key: "size", label: "Размер", sort: "size", num: true, cell: (t) => fmtBytes(t.runtime?.size) },
    {
      key: "progress",
      label: "Готово",
      sort: "progress",
      cell: (t) => {
        const pct = Math.max(0, Math.min(100, (t.runtime?.progress ?? 0) * 100));
        const wrap = el("div", { className: "ttable__progress" });
        const bar = el("div", { className: "progress" });
        bar.append(
          el("div", {
            className: `progress__bar${pct >= 99.95 ? " progress__bar--complete" : ""}`,
            style: `width:${pct}%`,
          }),
        );
        wrap.append(bar, el("span", { className: "ttable__progress-val" }, [`${pct.toFixed(0)}%`]));
        return wrap;
      },
    },
    { key: "uploaded", label: "Отдано", sort: "uploaded", num: true, cell: (t) => fmtBytes(t.runtime?.total_uploaded) },
    { key: "down", label: "↓", sort: "down", num: true, cell: (t) => fmtRate(t.runtime?.download_rate) },
    { key: "up", label: "↑", sort: "up", num: true, cell: (t) => fmtRate(t.runtime?.upload_rate) },
    { key: "seeds", label: "Сиды", num: true, cell: (t) => String(t.runtime?.num_seeds ?? 0) },
    { key: "peers", label: "Пиры", sort: "peers", num: true, cell: (t) => String(t.runtime?.peers ?? 0) },
    { key: "added", label: "Добавлен", sort: "added", cell: (t) => fmtAddedCell(t.created_at) },
    { key: "label", label: "Метка", cell: (t) => (t.label && t.label.trim() ? t.label : "—") },
  ];
}

function sortByHeader(sort: ListSort): void {
  if (listSort === sort) return;
  listSort = sort;
  lsSet("ui.sort", listSort);
  listPage = 0;
  listReload?.();
}

function renderTorrentTable(
  items: TorrentOut[],
  onChange: () => void,
  onSelectToggle: (id: number, checked: boolean) => void,
): HTMLElement {
  const cols = tableColumns();
  const writable = canWrite();
  const checks: HTMLInputElement[] = [];

  const headCells: HTMLElement[] = [];
  if (writable) {
    const selectAll = el("input", { type: "checkbox" }) as HTMLInputElement;
    selectAll.addEventListener("change", () => {
      for (const cb of checks) {
        if (cb.checked !== selectAll.checked) {
          cb.checked = selectAll.checked;
          onSelectToggle(Number(cb.dataset.id), cb.checked);
        }
      }
    });
    headCells.push(el("th", { className: "ttable__check" }, [selectAll]));
  }
  for (const c of cols) {
    const cls = `${c.num ? "ttable__num" : ""}${c.sort ? " ttable__sortable" : ""}`.trim();
    const th = el("th", cls ? { className: cls } : {});
    if (c.sort) {
      const active = listSort === c.sort;
      th.append(c.label);
      if (active) th.append(el("span", { className: "ttable__sort-ind" }, ["▼"]));
      th.addEventListener("click", () => sortByHeader(c.sort as ListSort));
      th.title = "Сортировать";
    } else {
      th.append(c.label);
    }
    headCells.push(th);
  }
  if (writable) headCells.push(el("th", { className: "ttable__actions-h" }, [""]));

  const thead = el("thead", {}, [el("tr", {}, headCells)]);
  const tbody = el("tbody");

  for (const t of items) {
    const cells: HTMLElement[] = [];
    if (writable) {
      const cb = el("input", { type: "checkbox" }) as HTMLInputElement;
      cb.dataset.id = String(t.id);
      cb.checked = selectedIds.has(t.id);
      cb.addEventListener("change", () => onSelectToggle(t.id, cb.checked));
      checks.push(cb);
      cells.push(el("td", { className: "ttable__check" }, [cb]));
    }
    for (const c of cols) {
      const td = el("td", c.num ? { className: "ttable__num" } : {});
      if (c.key === "name") td.classList.add("ttable__name");
      td.append(c.cell(t));
      cells.push(td);
    }
    if (writable) {
      const isPaused = t.status === "paused";
      const toggle = el("button", {
        type: "button",
        className: "btn btn--ghost btn--xs",
        title: isPaused ? "Старт" : "Пауза",
      }, [isPaused ? "▶" : "⏸"]);
      toggle.addEventListener("click", async () => {
        try {
          await fetchJson(`/torrents/${t.id}/${isPaused ? "resume" : "pause"}`, { method: "POST" });
          await onChange();
        } catch (e) {
          showToast(e instanceof Error ? e.message : String(e), true);
        }
      });
      const del = el("button", {
        type: "button",
        className: "btn btn--ghost btn--xs btn--danger-ghost",
        title: "Удалить",
      }, ["🗑"]);
      del.addEventListener("click", () => {
        void deleteTorrentWithDialog({ id: t.id, display_name: t.display_name }, onChange);
      });
      cells.push(el("td", { className: "ttable__actions" }, [toggle, del]));
    }
    tbody.append(el("tr", {}, cells));
  }

  const table = el("table", { className: "ttable" }, [thead, tbody]);
  return el("div", { className: "ttable-wrap" }, [table]);
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
  // Фильтрация/сортировка/пагинация — целиком на сервере (в т.ч. по «живым» полям из снимка).
  const shown = items;
  countEl.textContent = `${listTotal} ${listTotal === 1 ? "торрент" : listTotal < 5 ? "торрента" : "торрентов"}`;
  updateLiveMeta(metaEl, items);
  listEl.replaceChildren();
  if (shown.length === 0) {
    const hasFilter = Boolean(listSearch || listStatusFilter || listLabelFilter || listEngineFilter);
    listEl.append(
      el("div", { className: "empty-state" }, [
        el("p", {}, [listTotal === 0 && !hasFilter ? "Пока пусто" : "Ничего не найдено"]),
        el("p", {}, [
          listTotal === 0 && !hasFilter ? "Добавьте magnet, URL или .torrent ниже" : "Измените фильтр",
        ]),
      ]),
    );
    renderPager();
    return;
  }

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
  if (listView === "table") {
    listEl.append(renderTorrentTable(shown, refresh, onSelectToggle));
  } else {
    const ul = el("ul", { className: "torrent-list" });
    for (const t of shown) ul.append(renderTorrentCard(t, refresh, onSelectToggle));
    listEl.append(ul);
  }
  renderPager();
}

function renderPager(): void {
  if (!pagerHost) return;
  const pages = Math.max(1, Math.ceil(listTotal / listPageSize));
  if (listPage > pages - 1) listPage = pages - 1;
  if (listPage < 0) listPage = 0;
  const start = listTotal === 0 ? 0 : listPage * listPageSize + 1;
  const end = Math.min(listTotal, (listPage + 1) * listPageSize);

  const sizeSel = el("select", { className: "list-filter__select pager__size" }) as HTMLSelectElement;
  for (const n of PAGE_SIZES) {
    const o = el("option", { value: String(n) }, [`${n} / стр.`]) as HTMLOptionElement;
    if (n === listPageSize) o.selected = true;
    sizeSel.append(o);
  }
  sizeSel.addEventListener("change", () => {
    listPageSize = parseInt(sizeSel.value, 10) || 50;
    lsSet("ui.pageSize", String(listPageSize));
    listPage = 0;
    listReload?.();
  });

  const first = el("button", { type: "button", className: "btn btn--sm pager__btn" }, ["«"]) as HTMLButtonElement;
  const prev = el("button", { type: "button", className: "btn btn--sm pager__btn" }, ["‹"]) as HTMLButtonElement;
  const next = el("button", { type: "button", className: "btn btn--sm pager__btn" }, ["›"]) as HTMLButtonElement;
  const last = el("button", { type: "button", className: "btn btn--sm pager__btn" }, ["»"]) as HTMLButtonElement;
  first.disabled = prev.disabled = listPage <= 0;
  next.disabled = last.disabled = listPage >= pages - 1;
  const go = (p: number) => {
    listPage = Math.min(Math.max(0, p), pages - 1);
    listReload?.();
  };
  first.addEventListener("click", () => go(0));
  prev.addEventListener("click", () => go(listPage - 1));
  next.addEventListener("click", () => go(listPage + 1));
  last.addEventListener("click", () => go(pages - 1));

  const info = el("span", { className: "pager__info" }, [
    listTotal === 0 ? "0" : `${start}–${end} из ${listTotal}`,
    el("span", { className: "pager__page" }, [` · стр. ${listPage + 1}/${pages}`]),
  ]);

  pagerHost.replaceChildren(
    el("div", { className: "pager__left" }, [sizeSel]),
    el("div", { className: "pager__right" }, [info, first, prev, next, last]),
  );
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
    const params = new URLSearchParams();
    params.set("limit", String(listPageSize));
    params.set("offset", String(listPage * listPageSize));
    const q = listSearch.trim();
    if (q) params.set("q", q);
    if (listStatusFilter) params.set("status", listStatusFilter);
    if (listLabelFilter) params.set("label", listLabelFilter);
    if (listEngineFilter) params.set("engine_id", listEngineFilter);
    if (listState) params.set("state", listState);
    params.set("sort", listSort);
    const page = await fetchJson<TorrentPageOut>(`/torrents?${params.toString()}`, { signal });
    if (gen !== listLoadGeneration) return;
    listTotal = page.total;
    // Если из-за фильтра текущая страница ушла за пределы — вернёмся на последнюю и перезагрузим.
    const maxPage = Math.max(0, Math.ceil(listTotal / listPageSize) - 1);
    if (listPage > maxPage && listTotal > 0) {
      listPage = maxPage;
      void loadTorrents(listEl, countEl, metaEl, opts);
      return;
    }
    const items = page.items;
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

// Живая панель сверху. Приоритет — WebSocket (Фаза 7); если фича выключена/недоступна —
// откат на SSE; при ошибке SSE — откат на таймерный поллинг.
function applySessionStats(sessionBarHost: HTMLElement, stats?: SessionStats | null): void {
  if (parseRoute().view !== "list") return;
  if (stats) sessionBarHost.replaceChildren(mountSessionBar(stats));
}

function startListSse(sessionBarHost: HTMLElement, onFallback: () => void): void {
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
      // Поток отдаёт только агрегаты для живой панели сверху. Список грузится постранично
      // отдельным поллингом (см. scheduleListPoll), поэтому здесь его НЕ трогаем.
      const data = JSON.parse((ev as MessageEvent).data) as { stats: SessionStats };
      applySessionStats(sessionBarHost, data.stats);
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

function startListStream(_refs: ListHostRefs, sessionBarHost: HTMLElement, onFallback: () => void): void {
  stopListStream();
  if (wsAvailable()) {
    listStatsUnsub = wsSubscribe("stats", (msg) => {
      if (parseRoute().view !== "list") return;
      const data = msg.data as { stats?: SessionStats } | undefined;
      applySessionStats(sessionBarHost, data?.stats);
    });
    // Если WS окажется выключен/недоступен — переключаемся на SSE-поток.
    listUnavailOff = onWsUnavailable(() => {
      if (listStatsUnsub !== null) {
        listStatsUnsub();
        listStatsUnsub = null;
      }
      if (parseRoute().view === "list") startListSse(sessionBarHost, onFallback);
    });
    return;
  }
  startListSse(sessionBarHost, onFallback);
}

interface LabelCombo {
  /** Контрол для вставки в форму (селект + инлайн-поле «Новая метка»). */
  control: HTMLElement;
  /** Текущее значение метки (пусто, если «Без метки»). */
  value: () => string;
  /** Загрузить список меток с сервера и восстановить сохранённый выбор. */
  refresh: () => Promise<void>;
}

// Комбобокс метки: выпадающий список готовых меток + пункт «Новая метка…», при
// выборе которого список в той же строке превращается в поле ввода (кнопка ↩ —
// назад к списку). Опционально запоминает выбор в localStorage. Общий для окон
// «Добавить торрент» и «Создать торрент».
function createLabelCombo(opts?: { storageKey?: string; persist?: boolean }): LabelCombo {
  const NEW_LABEL = "__new__";
  const storageKey = opts?.storageKey ?? "ui.addLabel";
  const persist = opts?.persist ?? true;

  const select = el("select", { className: "select" }) as HTMLSelectElement;
  const input = el("input", {
    type: "text",
    placeholder: "Новая метка",
    className: "label-combo__input",
  }) as HTMLInputElement;
  const back = el("button", {
    type: "button",
    className: "btn btn--ghost btn--sm label-combo__back",
    title: "Выбрать из списка",
  }, ["↩"]) as HTMLButtonElement;
  const editWrap = el("div", { className: "label-combo__edit" }, [input, back]);
  editWrap.hidden = true;
  const control = el("div", { className: "label-combo" }, [select, editWrap]);

  let newMode = false;
  const value = () => (newMode ? input.value.trim() : select.value);
  const remember = () => {
    if (persist) lsSet(storageKey, value());
  };
  const showInput = () => {
    newMode = true;
    select.hidden = true;
    editWrap.hidden = false;
    input.focus();
  };
  const showSelect = () => {
    newMode = false;
    editWrap.hidden = true;
    select.hidden = false;
    if (select.value === NEW_LABEL) select.value = "";
    remember();
  };
  select.addEventListener("change", () => {
    if (select.value === NEW_LABEL) showInput();
    else remember();
  });
  input.addEventListener("input", remember);
  back.addEventListener("click", () => {
    input.value = "";
    showSelect();
  });

  const refresh = async () => {
    const saved = persist ? lsGet(storageKey) ?? "" : "";
    let labels: string[] = [];
    try {
      labels = await fetchJson<string[]>("/labels");
    } catch {
      /* список меток необязателен */
    }
    select.replaceChildren(
      el("option", { value: "" }, ["Без метки"]),
      ...labels.map((lb) => el("option", { value: lb }, [lb])),
      el("option", { value: NEW_LABEL }, ["Новая метка…"]),
    );
    if (saved && labels.includes(saved)) {
      select.value = saved;
      showSelectSilently();
    } else if (saved) {
      input.value = saved;
      select.value = NEW_LABEL;
      newMode = true;
      select.hidden = true;
      editWrap.hidden = false;
    } else {
      showSelectSilently();
    }
  };
  // Как showSelect, но без записи в localStorage (используется при восстановлении).
  const showSelectSilently = () => {
    newMode = false;
    editWrap.hidden = true;
    select.hidden = false;
  };

  return { control, value, refresh };
}

function mountAddPanel(savePathDefault: string, onAdded: (created?: TorrentOut) => void): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["Добавить торрент"]));
  const body = el("div", { className: "panel__body" });

  const tabs = el("div", { className: "tabs" });
  const tabFile = el("button", { type: "button", className: "tab tab--active", "data-tab": "file" }, ["Файл"]);
  const tabMagnet = el("button", { type: "button", className: "tab", "data-tab": "magnet" }, ["Magnet"]);
  const tabUrl = el("button", { type: "button", className: "tab", "data-tab": "url" }, ["URL"]);
  tabs.append(tabFile, tabMagnet, tabUrl);

  const magnetPanel = el("div", { className: "tab-panel", "data-panel": "magnet", hidden: "" });
  const urlPanel = el("div", { className: "tab-panel", "data-panel": "url", hidden: "" });
  const filePanel = el("div", { className: "tab-panel", "data-panel": "file" });

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
  // Метка: общий комбобокс (готовые метки + «Новая метка…»), помнит выбор в ui.addLabel.
  const labelCombo = createLabelCombo({ storageKey: "ui.addLabel" });
  const nameInput = el("input", { type: "text", placeholder: "Название (необязательно)" }) as HTMLInputElement;
  const torrentFile = el("input", { type: "file", accept: ".torrent", multiple: "" }) as HTMLInputElement;

  let activeTab: "magnet" | "url" | "file" = "file";
  const switchTab = (name: "magnet" | "url" | "file") => {
    activeTab = name;
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

  magnetPanel.append(field("Magnet-ссылка", magnetInput));
  urlPanel.append(field("Ссылка на .torrent", urlInput));
  filePanel.append(field("Файлы .torrent", torrentFile, "Можно выбрать сразу несколько файлов"));

  const advanced = el("details", { className: "advanced" });
  advanced.append(
    el("summary", {}, ["Дополнительно"]),
    field("Название", nameInput, "Если пусто — берётся из торрента"),
    field("Метка", labelCombo.control, "Выберите готовую или «Новая метка…» для своей"),
    field(
      "Свой путь",
      customPathInput,
      "Если задано — переопределяет выбор движка. Обычно /data/b1 для движка b1.",
    ),
  );

  const addBtn = el("button", { type: "button", className: "btn btn--primary add-submit" }, [
    "Добавить",
  ]) as HTMLButtonElement;

  body.append(
    tabs,
    filePanel,
    magnetPanel,
    urlPanel,
    field("Движок", engineSelect, "Куда сохранять — хранилище выбранного движка"),
    advanced,
    el("div", { className: "btn-row" }, [addBtn]),
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

  void labelCombo.refresh();

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

  const doMagnet = async () => {
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
          display_name: nameInput.value.trim(),
          label: labelCombo.value(),
        }),
      });
      magnetInput.value = "";
      nameInput.value = "";
      showToast("Торрент добавлен");
      onAdded(created);
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  };

  const doUrl = async () => {
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
          display_name: nameInput.value.trim(),
          label: labelCombo.value(),
        }),
      });
      urlInput.value = "";
      nameInput.value = "";
      showToast("Торрент загружен по URL");
      onAdded(created);
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  };

  const doFile = async () => {
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
        body.set("display_name", nameInput.value.trim());
        body.set("label", labelCombo.value());
        const res = await fetch(`${API}/torrents/upload`, { method: "POST", headers: apiHeaders(false), body });
        await throwIfNotOk(res);
        const created = (await res.json()) as TorrentOut;
        torrentFile.value = "";
        nameInput.value = "";
        showToast("Торрент загружен");
        onAdded(created);
        return;
      }

      const body = new FormData();
      for (const f of files) body.append("torrent_files", f, f.name);
      if (!applyTargetToForm(body)) return;
      body.set("label", labelCombo.value());
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
  };

  addBtn.addEventListener("click", () => {
    if (activeTab === "magnet") void doMagnet();
    else if (activeTab === "url") void doUrl();
    else void doFile();
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

const stripTorrentExt = (n: string): string => n.replace(/\.torrent$/i, "");

/** Поиск раздачи для замены: поле ввода + выпадающий список результатов из /torrents. */
function buildTorrentPicker(
  initialQuery: string,
  onChange: () => void,
): { el: HTMLElement; getId: () => number | null } {
  const wrap = el("div", { className: "torrent-picker" });
  const input = el("input", {
    type: "search",
    className: "list-filter__search",
    placeholder: "Поиск раздачи для замены…",
    value: initialQuery,
  }) as HTMLInputElement;
  const results = el("div", { className: "torrent-picker__results", hidden: "" });
  const chosenLbl = el("div", { className: "torrent-picker__chosen", hidden: "" });
  wrap.append(input, results, chosenLbl);
  let chosenId: number | null = null;
  let debounce: ReturnType<typeof setTimeout> | null = null;

  const candLine = (t: TorrentOut) => `${t.display_name} · ${t.engine_id} · ${t.save_path}`;

  const runSearch = async () => {
    const q = input.value.trim();
    if (!q) {
      results.hidden = true;
      results.replaceChildren();
      return;
    }
    try {
      const page = await fetchJson<TorrentPageOut>(
        `/torrents?q=${encodeURIComponent(q)}&limit=8&sort=name`,
      );
      results.replaceChildren();
      if (page.items.length === 0) {
        results.append(el("div", { className: "torrent-picker__empty" }, ["Ничего не найдено"]));
      } else {
        for (const t of page.items) {
          const opt = el("button", { type: "button", className: "torrent-picker__item" }, [candLine(t)]);
          opt.addEventListener("click", () => {
            chosenId = t.id;
            chosenLbl.replaceChildren(
              el("span", { className: "badge badge--seeding" }, ["выбрано"]),
              document.createTextNode(` ${t.display_name} · ${t.engine_id}`),
            );
            chosenLbl.hidden = false;
            results.hidden = true;
            input.value = t.display_name;
            onChange();
          });
          results.append(opt);
        }
      }
      results.hidden = false;
    } catch (e) {
      results.replaceChildren(
        el("div", { className: "torrent-picker__empty" }, [e instanceof Error ? e.message : String(e)]),
      );
      results.hidden = false;
    }
  };

  input.addEventListener("input", () => {
    chosenId = null;
    chosenLbl.hidden = true;
    onChange();
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(() => void runSearch(), 300);
  });
  void runSearch();

  return { el: wrap, getId: () => chosenId };
}

/** Строка одного файла в диалоге обновления: автосовпадение / выбор / ручной поиск. */
function buildUpdateRow(
  file: File,
  candidates: TorrentOut[],
  onChange: () => void,
): { el: HTMLElement; getTargetId: () => number | null } {
  const row = el("div", { className: "update-row" });
  row.append(el("div", { className: "update-row__file" }, [file.name]));
  const targetHost = el("div", { className: "update-row__target" });
  row.append(targetHost);

  const candLine = (t: TorrentOut) => `${t.display_name} · ${t.engine_id} · ${t.save_path}`;
  let getId: () => number | null = () => null;

  const showPicker = () => {
    const picker = buildTorrentPicker(stripTorrentExt(file.name), onChange);
    targetHost.replaceChildren(
      el("span", { className: "update-row__match" }, ["Выберите раздачу для замены:"]),
      picker.el,
    );
    getId = picker.getId;
    onChange();
  };

  if (candidates.length === 1) {
    const t = candidates[0];
    const changeLink = el("a", { href: "#", className: "update-row__change" }, ["изменить"]);
    changeLink.addEventListener("click", (ev) => {
      ev.preventDefault();
      showPicker();
    });
    targetHost.append(
      el("span", { className: "badge badge--seeding" }, ["найдено"]),
      el("span", { className: "update-row__match" }, [`→ ${candLine(t)}`]),
      changeLink,
    );
    getId = () => t.id;
  } else if (candidates.length > 1) {
    const sel = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
    for (const t of candidates) sel.append(el("option", { value: String(t.id) }, [candLine(t)]));
    const changeLink = el("a", { href: "#", className: "update-row__change" }, ["вручную"]);
    changeLink.addEventListener("click", (ev) => {
      ev.preventDefault();
      showPicker();
    });
    targetHost.append(
      el("span", { className: "badge badge--queued" }, [`совпадений: ${candidates.length}`]),
      sel,
      changeLink,
    );
    getId = () => (sel.value ? Number(sel.value) : null);
  } else {
    targetHost.append(el("span", { className: "badge badge--paused" }, ["не найдено"]));
    showPicker();
  }

  return { el: row, getTargetId: () => getId() };
}

/** Диалог «Обновить торрент»: загрузка новых .torrent и замена существующих раздач
    с сохранением движка/пути/скачанного (recheck докачивает только новое). */
function showUpdateTorrentDialog(onDone: () => void): void {
  const overlay = el("div", { className: "modal-overlay" });
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") close();
  };
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };

  const panel = el("div", { className: "panel modal-panel update-panel" });
  const closeBtn = el(
    "button",
    { type: "button", className: "btn btn--ghost btn--sm modal-close", "aria-label": "Закрыть" },
    ["✕"],
  );
  closeBtn.addEventListener("click", close);
  panel.append(el("div", { className: "panel__head panel__head--with-action" }, ["Обновить торрент", closeBtn]));
  const body = el("div", { className: "panel__body" });
  panel.append(body);
  body.append(
    el("p", { className: "field__hint update-intro" }, [
      "Загрузите новый .torrent (например, сезон с новой серией). Найду старую раздачу по имени " +
        "и заменю её — движок, путь и уже скачанное сохранятся, докачается только новое. " +
        "Если совпадение не найдено — выберите раздачу вручную.",
    ]),
  );

  const fileInput = el("input", { type: "file", accept: ".torrent", multiple: "" }) as HTMLInputElement;
  body.append(field("Новые .torrent-файлы", fileInput));

  const rowsHost = el("div", { className: "update-rows" });
  body.append(rowsHost);

  const replaceBtn = el("button", { type: "button", className: "btn btn--primary btn--sm" }, [
    "Заменить",
  ]) as HTMLButtonElement;
  replaceBtn.disabled = true;
  body.append(el("div", { className: "update-footer" }, [replaceBtn]));

  let rows: { file: File; getTargetId: () => number | null }[] = [];
  const syncReplaceBtn = () => {
    replaceBtn.disabled = rows.length === 0 || !rows.some((r) => r.getTargetId() != null);
  };

  fileInput.addEventListener("change", async () => {
    const files = fileInput.files ? Array.from(fileInput.files) : [];
    rows = [];
    rowsHost.replaceChildren();
    if (files.length === 0) {
      syncReplaceBtn();
      return;
    }
    const bad = files.filter((f) => !f.name.toLowerCase().endsWith(".torrent"));
    if (bad.length > 0) {
      showToast("Поддерживаются только .torrent-файлы", true);
    }
    const good = files.filter((f) => f.name.toLowerCase().endsWith(".torrent"));
    if (good.length === 0) {
      syncReplaceBtn();
      return;
    }
    rowsHost.append(el("p", { className: "field__hint" }, ["Поиск совпадений…"]));
    let result: UpdateMatchResult;
    try {
      result = await fetchJson<UpdateMatchResult>("/torrents/update/match", {
        method: "POST",
        body: JSON.stringify({ filenames: good.map((f) => f.name) }),
      });
    } catch (e) {
      rowsHost.replaceChildren(
        el("p", { className: "field__hint" }, [e instanceof Error ? e.message : String(e)]),
      );
      return;
    }
    rowsHost.replaceChildren();
    good.forEach((f, i) => {
      const ctl = buildUpdateRow(f, result.items[i]?.candidates ?? [], syncReplaceBtn);
      rows.push({ file: f, getTargetId: ctl.getTargetId });
      rowsHost.append(ctl.el);
    });
    syncReplaceBtn();
  });

  replaceBtn.addEventListener("click", async () => {
    const tasks = rows
      .map((r) => ({ file: r.file, id: r.getTargetId() }))
      .filter((t): t is { file: File; id: number } => t.id != null);
    if (tasks.length === 0) {
      showToast("Не выбрана раздача для замены", true);
      return;
    }
    replaceBtn.disabled = true;
    replaceBtn.textContent = "Заменяю…";
    let ok = 0;
    const errors: string[] = [];
    for (const t of tasks) {
      try {
        const body = new FormData();
        body.set("torrent_file", t.file, t.file.name);
        const res = await fetch(`${API}/torrents/${t.id}/replace`, {
          method: "POST",
          headers: apiHeaders(false),
          body,
        });
        await throwIfNotOk(res);
        ok += 1;
      } catch (e) {
        errors.push(`${t.file.name}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    if (ok > 0) {
      showToast(`Обновлено: ${ok}${errors.length ? `, ошибок: ${errors.length}` : ""}`, errors.length > 0);
    } else {
      showToast(errors[0] ?? "Не удалось обновить", true);
    }
    close();
    onDone();
  });

  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  document.addEventListener("keydown", onKey);
  overlay.append(panel);
  document.body.append(overlay);
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

  const refresh = (opts?: { afterAdd?: boolean }) => {
    void refreshFacets();
    void loadTorrents(listHost, countEl, metaEl, {
      silent: lastListItems.length > 0 && !opts?.afterAdd,
      fastPoll: opts?.afterAdd,
      scheduleNext: listRefs.scheduleNext,
    });
  };

  const repaint = () => paintTorrentList(listRefs, lastListItems);

  // Фильтр/поиск/сортировка/размер страницы меняют выборку на сервере — сбрасываем на
  // первую страницу и перезагружаем. Навигация по страницам идёт через listReload.
  const reloadFromFilters = () => {
    listPage = 0;
    void refresh();
  };
  listReload = () => void refresh();

  const onAdded = (created?: TorrentOut) => {
    if (created) showTorrentInList(listRefs, created);
    void refresh({ afterAdd: true });
  };

  let searchDebounce: ReturnType<typeof setTimeout> | null = null;
  const searchInput = el("input", {
    type: "search",
    placeholder: "Поиск по названию, метке, hash…",
    className: "list-filter__search",
    value: listSearch,
  }) as HTMLInputElement;
  searchInput.addEventListener("input", () => {
    listSearch = searchInput.value;
    lsSet("ui.search", listSearch);
    syncReset();
    if (searchDebounce) clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => reloadFromFilters(), 300);
  });

  const statusSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["", "Все статусы"],
    ["seeding", "Раздача"],
    ["downloading", "Загрузка"],
    ["paused", "Пауза"],
  ]) {
    const o = el("option", { value: val }, [label]) as HTMLOptionElement;
    o.dataset.base = label;
    if (val === listStatusFilter) o.selected = true;
    statusSelect.append(o);
  }
  statusSelect.addEventListener("change", () => {
    listStatusFilter = statusSelect.value;
    lsSet("ui.status", listStatusFilter);
    reloadFromFilters();
    syncReset();
  });

  const labelSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  const labelSuggestions = el("datalist", { id: "label-suggestions" }) as HTMLDataListElement;
  const reloadLabels = async () => {
    const allOpt = el("option", { value: "" }, ["Все метки"]) as HTMLOptionElement;
    allOpt.dataset.base = "Все метки";
    labelSelect.replaceChildren(allOpt);
    labelSuggestions.replaceChildren();
    try {
      const labels = await fetchJson<string[]>("/labels");
      for (const lb of labels) {
        const o = el("option", { value: lb }, [lb]) as HTMLOptionElement;
        o.dataset.base = lb;
        if (lb === listLabelFilter) o.selected = true;
        labelSelect.append(o);
        labelSuggestions.append(el("option", { value: lb }));
      }
    } catch {
      /* ignore */
    }
    applyFacetCounts();
  };
  labelSelect.addEventListener("change", () => {
    listLabelFilter = labelSelect.value;
    lsSet("ui.label", listLabelFilter);
    reloadFromFilters();
    syncReset();
  });

  const engineSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  // Опции движков динамические — берём из facets.engines (id → кол-во раздач).
  const reloadEngines = (): void => {
    const allOpt = el("option", { value: "" }, ["Все движки"]) as HTMLOptionElement;
    allOpt.dataset.base = "Все движки";
    engineSelect.replaceChildren(allOpt);
    const ids = facets ? Object.keys(facets.engines).sort() : [];
    // Выбранный движок мог пропасть из facets (0 раздач) — покажем его всё равно.
    if (listEngineFilter && !ids.includes(listEngineFilter)) ids.push(listEngineFilter);
    for (const id of ids) {
      const o = el("option", { value: id }, [id]) as HTMLOptionElement;
      o.dataset.base = id;
      if (id === listEngineFilter) o.selected = true;
      engineSelect.append(o);
    }
    applyFacetCounts();
  };
  engineSelect.addEventListener("change", () => {
    listEngineFilter = engineSelect.value;
    lsSet("ui.engine", listEngineFilter);
    reloadFromFilters();
    syncReset();
  });

  const sortSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["name", "Сорт: имя"],
    ["added", "Сорт: новые"],
    ["up", "Сорт: скорость"],
    ["down", "Сорт: скачивание"],
    ["peers", "Сорт: пиры"],
    ["uploaded", "Сорт: раздано всего"],
    ["ratio", "Сорт: рейтинг"],
    ["size", "Сорт: размер"],
    ["progress", "Сорт: прогресс"],
  ]) {
    const o = el("option", { value: val }, [label]) as HTMLOptionElement;
    if (val === listSort) o.selected = true;
    sortSelect.append(o);
  }
  sortSelect.addEventListener("change", () => {
    listSort = sortSelect.value as ListSort;
    lsSet("ui.sort", listSort);
    reloadFromFilters();
    syncReset();
  });

  const stateSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  for (const [val, label] of [
    ["", "Состояние: все"],
    ["active", "Активные (отдача)"],
    ["peers", "Есть пиры"],
    ["idle", "Простаивают"],
    ["incomplete", "Незавершённые"],
    ["error", "С ошибкой"],
  ]) {
    const o = el("option", { value: val }, [label]) as HTMLOptionElement;
    o.dataset.base = label;
    if (val === listState) o.selected = true;
    stateSelect.append(o);
  }
  stateSelect.addEventListener("change", () => {
    listState = stateSelect.value as ListState;
    lsSet("ui.state", listState);
    reloadFromFilters();
    syncReset();
  });

  // Счётчики у вариантов фильтров: B1 (1 720 шт), есть трафик (40 шт), Раздача (8 000 шт)…
  type ListFacets = {
    total: number;
    statuses: Record<string, number>;
    labels: Record<string, number>;
    engines: Record<string, number>;
    states: Record<string, number>;
  };
  let facets: ListFacets | null = null;
  let lastFacetsAt = 0;
  const fmtCount = (n: number) => ` (${n.toLocaleString("ru-RU")} шт)`;
  function applyCountsTo(sel: HTMLSelectElement, counts: Record<string, number>): void {
    for (const o of Array.from(sel.options)) {
      const base = o.dataset.base ?? o.textContent ?? "";
      const c = o.value === "" ? facets?.total : counts[o.value];
      o.textContent = c == null ? base : base + fmtCount(c);
    }
  }
  function applyFacetCounts(): void {
    if (!facets) return;
    applyCountsTo(statusSelect, facets.statuses);
    applyCountsTo(stateSelect, facets.states);
    applyCountsTo(labelSelect, facets.labels);
    applyCountsTo(engineSelect, facets.engines);
  }
  async function refreshFacets(): Promise<void> {
    const now = Date.now();
    if (now - lastFacetsAt < 5000) return; // не дёргаем чаще раза в 5с
    lastFacetsAt = now;
    try {
      facets = await fetchJson<ListFacets>("/torrents/facets");
      reloadEngines();
    } catch {
      /* счётчики необязательны — молча игнорируем */
    }
  }

  // Единый сегментированный переключатель вида: Плитка / Карточки / Таблица.
  // Заменяет прежние две отдельные иконки (вид + плотность) — один клик сразу
  // выбирает нужный режим, активный сегмент подсвечен.
  type ViewPreset = "grid" | "list" | "table";
  const presets: { p: ViewPreset; ic: keyof typeof ICON_PATHS; title: string }[] = [
    { p: "grid", ic: "grid", title: "Плитка" },
    { p: "list", ic: "rows", title: "Карточки" },
    { p: "table", ic: "table", title: "Таблица" },
  ];
  const currentPreset = (): ViewPreset =>
    listView === "table" ? "table" : listDensity === "compact" ? "grid" : "list";
  const segBtns = presets.map((s) => {
    const b = el("button", {
      type: "button",
      className: "view-switch__btn",
      title: s.title,
      "aria-label": s.title,
    }, [icon(s.ic)]) as HTMLButtonElement;
    b.addEventListener("click", () => setPreset(s.p));
    return { p: s.p, b };
  });
  const viewSwitch = el("div", { className: "view-switch", role: "group", "aria-label": "Вид списка" },
    segBtns.map((x) => x.b));
  const applyView = () => {
    const table = listView === "table";
    listHost.classList.toggle("torrent-list--table", table);
    listHost.classList.toggle("torrent-list--compact", !table && listDensity === "compact");
    // Табличный вид разворачиваем на всю ширину страницы — иначе колонки режутся.
    document.body.classList.toggle("layout-wide", table);
    const active = currentPreset();
    for (const x of segBtns) {
      const on = x.p === active;
      x.b.classList.toggle("is-active", on);
      x.b.setAttribute("aria-pressed", String(on));
    }
  };
  function setPreset(p: ViewPreset): void {
    if (p === currentPreset()) return;
    if (p === "table") {
      listView = "table";
    } else {
      listView = "cards";
      listDensity = p === "grid" ? "compact" : "comfortable";
      lsSet("ui.density", listDensity);
    }
    lsSet("ui.view", listView);
    applyView();
    repaint();
  }

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

  const createTorrentBtn = el("button", { type: "button", className: "btn btn--sm" }, [
    icon("file-plus"),
    "Создать торрент",
  ]);
  createTorrentBtn.addEventListener("click", () => openCreateTorrentDialog(() => void refresh({ afterAdd: true })));

  const creatorQueueBtn = el("button", { type: "button", className: "btn btn--sm" }, [
    icon("list"),
    "Очередь создания",
  ]);
  creatorQueueBtn.addEventListener("click", () => openCreatorQueueDialog(() => void refresh({ afterAdd: true })));

  const updateTorrentBtn = el("button", { type: "button", className: "btn btn--sm" }, [
    icon("upload"),
    "Обновить торрент",
  ]);
  updateTorrentBtn.addEventListener("click", () => showUpdateTorrentDialog(() => refresh({ afterAdd: true })));

  const settingsLink = el("button", { type: "button", className: "btn btn--ghost btn--sm" }, [icon("settings"), "Настройки"]);
  settingsLink.addEventListener("click", () => {
    setHashSettings();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  const headerActions = el("div", { className: "app-header__actions" });
  if (canWrite()) headerActions.append(addTorrentBtn, createTorrentBtn, creatorQueueBtn, updateTorrentBtn);
  headerActions.append(settingsLink, metaEl);
  const header = el("header", { className: "app-header" }, [
    brandLockup(),
    headerActions,
  ]);

  const resetFilters = el("button", {
    type: "button",
    className: "btn btn--ghost btn--sm filter-chips__reset",
  }, ["Сбросить всё"]);
  resetFilters.addEventListener("click", () => {
    listSearch = "";
    listStatusFilter = "";
    listLabelFilter = "";
    listEngineFilter = "";
    listState = "";
    listSort = "name";
    for (const k of ["ui.search", "ui.status", "ui.label", "ui.engine", "ui.state", "ui.sort"]) lsSet(k, "");
    searchInput.value = "";
    statusSelect.value = "";
    stateSelect.value = "";
    labelSelect.value = "";
    engineSelect.value = "";
    sortSelect.value = "name";
    closePopover();
    reloadFromFilters();
    syncReset();
  });

  const refreshBtn = el("button", {
    type: "button",
    className: "btn btn--ghost btn--sm list-controls__icon",
    title: "Обновить",
    "aria-label": "Обновить",
  }, [icon("refresh")]);
  refreshBtn.addEventListener("click", () => void refresh());

  // Вторичные фильтры (статус/состояние/метка) спрятаны в поповер — панель остаётся чистой,
  // а активные фильтры показываются «чипсами» под строкой поиска и снимаются в один клик.
  const popover = el("div", { className: "filter-popover", hidden: "" }, [
    el("label", { className: "filter-popover__field" }, [el("span", {}, ["Статус"]), statusSelect]),
    el("label", { className: "filter-popover__field" }, [el("span", {}, ["Состояние"]), stateSelect]),
    el("label", { className: "filter-popover__field" }, [el("span", {}, ["Метка"]), labelSelect]),
    el("label", { className: "filter-popover__field" }, [el("span", {}, ["Движок"]), engineSelect]),
  ]);
  const filterBadge = el("span", { className: "filter-btn__badge", hidden: "" });
  const filterBtn = el("button", {
    type: "button",
    className: "btn btn--ghost btn--sm filter-btn",
  }, [icon("filter"), el("span", {}, ["Фильтры"]), filterBadge]) as HTMLButtonElement;
  const filterWrap = el("div", { className: "filter-wrap" }, [filterBtn, popover]);

  let popoverOpen = false;
  const onDocClick = (ev: Event) => {
    if (!filterWrap.contains(ev.target as Node)) closePopover();
  };
  const onEsc = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") closePopover();
  };
  function closePopover(): void {
    if (!popoverOpen) return;
    popoverOpen = false;
    popover.hidden = true;
    filterBtn.classList.remove("is-open");
    document.removeEventListener("click", onDocClick);
    document.removeEventListener("keydown", onEsc);
  }
  function openPopover(): void {
    popoverOpen = true;
    popover.hidden = false;
    filterBtn.classList.add("is-open");
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onEsc);
  }
  filterBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (popoverOpen) closePopover();
    else openPopover();
  });

  const STATUS_LABELS: Record<string, string> = {
    seeding: "Раздача",
    downloading: "Загрузка",
    paused: "Пауза",
  };
  const STATE_LABELS: Record<string, string> = {
    active: "Активные",
    peers: "Есть пиры",
    idle: "Простаивают",
    incomplete: "Незавершённые",
    error: "С ошибкой",
  };
  const makeChip = (text: string, onRemove: () => void): HTMLElement => {
    const chip = el("span", { className: "filter-chip" }, [text]);
    const x = el("button", { type: "button", className: "filter-chip__x", "aria-label": "Убрать" }, ["✕"]);
    x.addEventListener("click", onRemove);
    chip.append(x);
    return chip;
  };

  const chipsRow = el("div", { className: "filter-chips", hidden: "" });

  // syncReset обновляет всё состояние UI фильтров: бейдж на кнопке, чипсы, кнопку сброса.
  function syncReset(): void {
    const count = [listStatusFilter, listState, listLabelFilter, listEngineFilter].filter(Boolean).length;
    filterBadge.hidden = count === 0;
    filterBadge.textContent = String(count);
    filterBtn.classList.toggle("filter-btn--has", count > 0);

    const chips: HTMLElement[] = [];
    if (listStatusFilter) {
      chips.push(
        makeChip(`Статус: ${STATUS_LABELS[listStatusFilter] ?? listStatusFilter}`, () => {
          listStatusFilter = "";
          statusSelect.value = "";
          lsSet("ui.status", "");
          reloadFromFilters();
          syncReset();
        }),
      );
    }
    if (listState) {
      chips.push(
        makeChip(`Состояние: ${STATE_LABELS[listState] ?? listState}`, () => {
          listState = "" as ListState;
          stateSelect.value = "";
          lsSet("ui.state", "");
          reloadFromFilters();
          syncReset();
        }),
      );
    }
    if (listLabelFilter) {
      chips.push(
        makeChip(`Метка: ${listLabelFilter}`, () => {
          listLabelFilter = "";
          labelSelect.value = "";
          lsSet("ui.label", "");
          reloadFromFilters();
          syncReset();
        }),
      );
    }
    if (listEngineFilter) {
      chips.push(
        makeChip(`Движок: ${listEngineFilter}`, () => {
          listEngineFilter = "";
          engineSelect.value = "";
          lsSet("ui.engine", "");
          reloadFromFilters();
          syncReset();
        }),
      );
    }

    const anyActive =
      Boolean(listSearch || listStatusFilter || listLabelFilter || listEngineFilter || listState) ||
      listSort !== "name";
    chipsRow.replaceChildren(...chips);
    if (anyActive) chipsRow.append(resetFilters);
    chipsRow.hidden = !anyActive;
  }

  const searchField = el("div", { className: "list-controls__search" }, [icon("search"), searchInput]);
  const filters = el("div", { className: "list-controls" }, [
    searchField,
    el("div", { className: "list-controls__actions" }, [filterWrap, sortSelect, viewSwitch, refreshBtn]),
    labelSuggestions,
  ]);
  applyView();

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

  const pager = el("div", { className: "list-pager" });
  pagerHost = pager;

  root.append(
    header,
    sessionBarHost,
    filters,
    chipsRow,
    bulkBar,
    listHost,
    pager,
  );
  syncReset();

  reloadEngines();
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

    // Пока живёт карточка переноса (её флаг data-active="1") — не пересобираем деталь, иначе
    // виджет пересоздаётся (мигает «Подготовка…») или на фазе finalizing (в БД уже seeding)
    // вместо завершения показался бы селектор движка. Виджет сам снимет флаг по active=false.
    if (
      container.childElementCount > 0 &&
      container.querySelector(".migrate-progress[data-active='1']")
    ) {
      scheduleNext?.(data);
      return;
    }

    const progress = data.runtime?.progress ?? 0;
    const pct = Math.round(progress * 1000) / 10;

    if (container.childElementCount > 0) saveDetailSpoilerStateFromDom(container, id);
    // Пересобираем дерево — снимаем прежнюю WS-подписку на эту раздачу (заведём новую ниже).
    detailWsOff?.();
    detailWsOff = null;
    container.replaceChildren();
    const hero = el("section", { className: "detail-hero panel" });
    const body = el("div", { className: "panel__body" });
    hero.append(body);

    const barFill = el("div", {
      className: `progress__bar${pct >= 100 ? " progress__bar--complete" : ""}`,
      style: `width:${pct}%`,
    });
    const bar = el("div", { className: "progress" }, [barFill]);

    const backRefresh = () => loadDetail(id, container, metaEl, scheduleNext);
    const st = effectiveStatus(data);
    const migrating = data.status === "migrating";

    let title = data.display_name || `Торрент #${data.id}`;
    if (title.toLowerCase().endsWith(".torrent")) title = title.slice(0, -".torrent".length);

    const titleEl = el("h1", { className: "detail-title" }, [title]);
    const titleWrap = el("div", { className: "detail-title-wrap" }, [titleEl]);
    if (canWrite()) {
      const editBtn = el("button", {
        type: "button",
        className: "btn btn--ghost btn--sm detail-title-edit",
        title: "Переименовать",
        "aria-label": "Переименовать",
      }, [icon("edit")]);
      editBtn.addEventListener("click", () => {
        if (titleWrap.querySelector(".detail-title-pop")) return;
        const input = el("input", {
          type: "text",
          className: "detail-title-input",
          value: data.display_name || "",
        }) as HTMLInputElement;
        const save = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Сохранить"]);
        const cancel = el("button", { type: "button", className: "btn btn--sm" }, ["Отмена"]);
        const editor = el("div", { className: "detail-title-editor detail-title-pop" }, [input, save, cancel]);
        editBtn.disabled = true;
        titleWrap.append(editor);
        input.focus();
        input.select();
        const abort = () => {
          editor.remove();
          editBtn.disabled = false;
        };
        const commit = async () => {
          const v = input.value.trim();
          if (!v) {
            showToast("Название не может быть пустым", true);
            return;
          }
          save.disabled = true;
          cancel.disabled = true;
          try {
            await fetchJson(`/torrents/${id}`, {
              method: "PATCH",
              body: JSON.stringify({ display_name: v }),
            });
            await backRefresh();
          } catch (err) {
            showToast(err instanceof Error ? err.message : String(err), true);
            save.disabled = false;
            cancel.disabled = false;
          }
        };
        save.addEventListener("click", () => void commit());
        cancel.addEventListener("click", abort);
        input.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter") void commit();
          else if (ev.key === "Escape") abort();
        });
      });
      titleWrap.append(editBtn);
    }

    const badgeEl = el("span", { className: badgeClass(st) }, [displayStatusLabel(data)]);
    const head = el("div", { className: "detail-head" }, [badgeEl, titleWrap]);

    // Статичные факты — компактной подстрокой, без отдельных боксов.
    const subParts = [`#${data.id}`, `движок ${data.engine_id}`, fmtBytes(data.runtime?.size)];
    const ratioStr = fmtRatio(data.runtime?.ratio);
    if (ratioStr !== "—") subParts.push(`рейтинг ${ratioStr}`);
    if (data.runtime?.added_time) {
      subParts.push(`добавлено ${new Date(data.runtime.added_time * 1000).toLocaleDateString("ru-RU")}`);
    }
    const sub = el("div", { className: "detail-sub" }, [subParts.join("  ·  ")]);

    const pctEl = el("span", { className: "detail-progress__pct" }, [fmtPercent(progress)]);
    const progressWrap = el("div", { className: "detail-progress" });
    progressWrap.append(bar, pctEl);
    if (st === "downloading") {
      const eta = fmtEta(data.runtime?.eta);
      if (eta !== "—") progressWrap.append(el("span", { className: "detail-progress__eta" }, [`ETA ${eta}`]));
    }

    // Живые показатели — чипами; «скачано» только если реально качали.
    const chips = el("div", { className: "detail-chips" });
    const dlChip = statChip(`↓ ${fmtRate(data.runtime?.download_rate)}`, "Скачивание", "dl");
    const ulChip = statChip(`↑ ${fmtRate(data.runtime?.upload_rate)}`, "Отдача", "ul");
    const peersChip = statChip(`${data.runtime?.num_seeds ?? 0} / ${data.runtime?.peers ?? 0}`, "Сиды / пиры");
    const uploadedChip = statChip(fmtBytes(data.runtime?.total_uploaded), "Отдано всего");
    chips.append(dlChip, ulChip, peersChip, uploadedChip);
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

    // Метка — редактируемая инлайн (плашка с карандашом, как заголовок).
    const labelWrap = el("div", { className: "detail-label-wrap" });
    const renderLabelView = () => {
      const text = data.label?.trim() || "";
      const chip = el(
        "span",
        { className: `detail-label-chip${text ? "" : " detail-label-chip--empty"}` },
        [text || "Без метки"],
      );
      const children: (string | Node)[] = [chip];
      if (canWrite()) {
        const editBtn = el("button", {
          type: "button",
          className: "btn btn--ghost btn--sm detail-title-edit",
          title: "Изменить метку",
          "aria-label": "Изменить метку",
        }, [icon("edit")]);
        editBtn.addEventListener("click", renderLabelEdit);
        children.push(editBtn);
      }
      labelWrap.replaceChildren(...children);
    };
    const renderLabelEdit = () => {
      const input = el("input", {
        type: "text",
        className: "detail-label-input",
        placeholder: "Без метки",
        value: data.label || "",
      }) as HTMLInputElement;
      const save = el("button", { type: "button", className: "btn btn--sm btn--primary" }, ["Сохранить"]);
      const cancel = el("button", { type: "button", className: "btn btn--sm" }, ["Отмена"]);
      labelWrap.replaceChildren(
        el("div", { className: "detail-title-editor detail-label-editor" }, [input, save, cancel]),
      );
      input.focus();
      input.select();
      const commit = async () => {
        save.disabled = true;
        cancel.disabled = true;
        try {
          await fetchJson(`/torrents/${id}`, {
            method: "PATCH",
            body: JSON.stringify({ label: input.value.trim() }),
          });
          await backRefresh();
        } catch (err) {
          showToast(err instanceof Error ? err.message : String(err), true);
          save.disabled = false;
          cancel.disabled = false;
        }
      };
      save.addEventListener("click", () => void commit());
      cancel.addEventListener("click", renderLabelView);
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") void commit();
        else if (ev.key === "Escape") renderLabelView();
      });
    };
    renderLabelView();
    head.append(labelWrap);

    const manageCard = (heading: string, node: HTMLElement) =>
      el("div", { className: "manage-card" }, [
        el("div", { className: "manage-card__title" }, [heading]),
        node,
      ]);

    const manage = el("div", { className: "detail-manage" }, [
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

    // WS (Фаза 7, WS-4): живые поля раздачи приходят пушем torrent:{id} (бэкенд — адресный
    // пуллер ws_pollers). Обновляем узлы на месте, без пересборки дерева. Полная пересборка
    // (структура: пиры/файлы/трекеры) остаётся редким бэкстопом-поллингом.
    const setChip = (chip: HTMLElement, text: string) => {
      const v = chip.querySelector(".stat-chip__value");
      if (v) v.textContent = text;
    };
    const applyLive = (rt: RuntimeOut | null | undefined, status?: string) => {
      if (!document.body.contains(badgeEl)) return;
      data.runtime = rt ?? null;
      if (status) data.status = status;
      const stLive = effectiveStatus(data);
      badgeEl.className = badgeClass(stLive);
      badgeEl.textContent = displayStatusLabel(data);
      const prog = data.runtime?.progress ?? 0;
      const pctNum = Math.round(prog * 1000) / 10;
      barFill.className = `progress__bar${pctNum >= 100 ? " progress__bar--complete" : ""}`;
      barFill.style.width = `${pctNum}%`;
      pctEl.textContent = fmtPercent(prog);
      setChip(dlChip, `↓ ${fmtRate(data.runtime?.download_rate)}`);
      setChip(ulChip, `↑ ${fmtRate(data.runtime?.upload_rate)}`);
      setChip(peersChip, `${data.runtime?.num_seeds ?? 0} / ${data.runtime?.peers ?? 0}`);
      setChip(uploadedChip, fmtBytes(data.runtime?.total_uploaded));
    };
    detailWsOff = wsSubscribe(`torrent:${id}`, (msg) => {
      const d = msg.data as { runtime?: RuntimeOut | null; status?: string } | undefined;
      if (d && !migrating) applyLive(d.runtime, d.status);
    });

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

  const back = el("a", { href: "/", className: "back-link" }, ["← Назад к списку"]);
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

function fmtBuildTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

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
  if (c.version) {
    card.append(
      el("div", { className: "health-card__ver", title: c.built_at ? `Собрано ${fmtBuildTime(c.built_at)}` : "" }, [
        `v${c.version}${c.built_at ? ` · ${fmtBuildTime(c.built_at)}` : ""}`,
      ]),
    );
  }
  if (c.kind === "engine") {
    const eid = c.engine_id ?? c.id;
    card.classList.add("health-card--clickable");
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.title = "Подробнее о движке";
    card.append(el("span", { className: "health-card__more" }, ["Подробнее →"]));
    const open = () => showEngineDetailModal(eid, c.name);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        open();
      }
    });
  }
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

let webBuildTime: string | null = null;
let webBuildTimeFetched = false;

async function ensureWebBuildTime(): Promise<void> {
  if (webBuildTimeFetched) return;
  webBuildTimeFetched = true;
  try {
    const res = await fetch("/BUILD_TIME", { cache: "no-store" });
    if (res.ok) webBuildTime = (await res.text()).trim() || null;
  } catch {
    /* статика может отсутствовать в dev — версии достаточно */
  }
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
    const webComp: HealthComponent = {
      id: "web",
      name: "Веб-интерфейс",
      kind: "core",
      status: "ok",
      detail: "Загружен в браузере",
      version: WEB_VERSION,
      built_at: webBuildTime,
    };
    grid.replaceChildren();
    grid.append(el("div", { className: "health-grid__label" }, ["Ядро"]));
    const coreRow = el("div", { className: "health-cards" });
    for (const c of core) coreRow.append(healthCard(c));
    coreRow.append(healthCard(webComp));
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
        // При живом WS канал engines пушит health сам — поллинг реже (бэкстоп).
        settingsHealthTimer = setTimeout(() => void tick(), wsAvailable() ? 20000 : 5000);
      }
    }
  };

  refreshBtn.addEventListener("click", () => void tick(true));
  void ensureWebBuildTime().then(() => void tick());
  // WS (Фаза 7, WS-4): health движков/ядра пушем через канал engines (бэкенд — ws_pollers).
  settingsEnginesUnsub?.();
  settingsEnginesUnsub = wsSubscribe("engines", (msg) => {
    if (parseRoute().view !== "settings") return;
    const d = msg.data as HealthFull | undefined;
    if (d && Array.isArray(d.components)) paint(d);
  });
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

const CREATE_POLL_MS = 1500;
const TERMINAL_CREATE_STATUSES = new Set(["completed", "failed", "cancelled"]);

async function downloadCreatedTorrent(task: CreatorTaskOut): Promise<void> {
  const res = await fetch(`${API}/creator/tasks/${task.engine_id}/${task.id}/download`, {
    headers: apiHeaders(false),
  });
  await throwIfNotOk(res);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: `${task.name || "torrent"}.torrent` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function openCreateTorrentDialog(onSeeded: () => void): void {
  const overlay = el("div", { className: "modal-overlay" });
  const dialog = el("div", {
    className: "modal-dialog modal-dialog--wide",
    role: "dialog",
    "aria-modal": "true",
    "aria-labelledby": "create-dialog-title",
  });

  let engines: EngineOut[] = [];
  let currentEngine = "";
  let currentPath = "";
  const selected = new Set<string>();

  const engineSelect = el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  const breadcrumb = el("div", { className: "creator-breadcrumb" });
  const listBox = el("div", { className: "creator-browser" });
  const selectionInfo = el("span", { className: "field__hint" }, ["Ничего не выбрано"]);

  const labelCombo = createLabelCombo({ storageKey: "ui.createLabel" });
  const nameInput = el("input", { type: "text", placeholder: "Название (только для одиночного)" }) as HTMLInputElement;

  const modeSeed = el("input", { type: "radio", name: "create-mode", value: "seed" }) as HTMLInputElement;
  const modeDownload = el("input", { type: "radio", name: "create-mode", value: "download" }) as HTMLInputElement;
  modeDownload.checked = true;
  const modeRow = el("div", { className: "creator-modes" }, [
    el("label", {}, [modeDownload, " Только создать (.torrent)"]),
    el("label", {}, [modeSeed, " Создать и раздавать"]),
  ]);
  // Метка и название — только для режима «Создать и раздавать».
  const seedFields = el("div", { className: "creator-seed-fields" }, [
    field("Метка", labelCombo.control, "Выберите готовую или «Новая метка…» для своей"),
    field("Название", nameInput),
  ]);
  void labelCombo.refresh();
  const syncModeFields = () => {
    seedFields.hidden = !modeSeed.checked;
  };
  modeSeed.addEventListener("change", syncModeFields);
  modeDownload.addEventListener("change", syncModeFields);
  syncModeFields();

  const episodeCheck = el("input", { type: "checkbox" }) as HTMLInputElement;
  const episodeRow = el("label", { className: "creator-check" }, [episodeCheck, " Проверять последовательность серий"]);

  const progressBox = el("div", { className: "creator-progress" });

  const createBtn = el("button", { type: "button", className: "btn btn--primary" }, ["Создать"]);
  const closeBtn = el("button", { type: "button", className: "btn btn--ghost" }, ["Закрыть"]);

  const updateSelectionInfo = () => {
    selectionInfo.textContent =
      selected.size === 0 ? "Ничего не выбрано" : `Выбрано: ${selected.size}`;
    createBtn.disabled = selected.size === 0;
  };

  const renderBreadcrumb = () => {
    breadcrumb.replaceChildren();
    const parts = currentPath ? currentPath.split("/") : [];
    const rootLink = el("button", { type: "button", className: "creator-crumb" }, ["/ (диск)"]);
    rootLink.addEventListener("click", () => void navigate(""));
    breadcrumb.append(rootLink);
    let acc = "";
    for (const p of parts) {
      acc = acc ? `${acc}/${p}` : p;
      const target = acc;
      breadcrumb.append(document.createTextNode(" / "));
      const link = el("button", { type: "button", className: "creator-crumb" }, [p]);
      link.addEventListener("click", () => void navigate(target));
      breadcrumb.append(link);
    }
  };

  const renderItems = (items: CreatorBrowseItem[]) => {
    listBox.replaceChildren();
    if (items.length === 0) {
      listBox.append(el("div", { className: "creator-empty" }, ["Пусто"]));
      return;
    }
    for (const item of items) {
      const row = el("div", { className: "creator-row" });
      const check = el("input", { type: "checkbox" }) as HTMLInputElement;
      check.checked = selected.has(item.path);
      check.addEventListener("change", () => {
        if (check.checked) selected.add(item.path);
        else selected.delete(item.path);
        updateSelectionInfo();
      });
      const ic = item.is_dir ? "📁" : "📄";
      const nameEl = item.is_dir
        ? el("button", { type: "button", className: "creator-name creator-name--dir" }, [`${ic} ${item.name}`])
        : el("span", { className: "creator-name" }, [`${ic} ${item.name}`]);
      if (item.is_dir) {
        (nameEl as HTMLButtonElement).addEventListener("click", () => void navigate(item.path));
      }
      const meta = el("span", { className: "creator-row__meta" }, [
        item.is_dir ? "" : fmtBytes(item.size),
      ]);
      row.append(check, nameEl, meta);
      listBox.append(row);
    }
  };

  const navigate = async (path: string, fallbackToRoot = false) => {
    currentPath = path;
    renderBreadcrumb();
    listBox.replaceChildren(el("div", { className: "creator-empty" }, ["Загрузка…"]));
    try {
      const items = await fetchJson<CreatorBrowseItem[]>(
        `/creator/browse?engine_id=${encodeURIComponent(currentEngine)}&path=${encodeURIComponent(path)}`,
      );
      renderItems(items);
    } catch (e) {
      if (fallbackToRoot && path !== "") {
        await navigate("");
        return;
      }
      listBox.replaceChildren(
        el("div", { className: "creator-empty" }, [e instanceof Error ? e.message : String(e)]),
      );
    }
  };

  // Папка контента движка внутри его SEEDING_DATA_ROOT (напр. /data/a1 → "a1"),
  // чтобы сразу открывать её, а не корень /data со служебными каталогами.
  const engineSubdir = (id: string): string => {
    const eng = engines.find((e) => e.id === id);
    const prefix = (eng?.storage_prefix ?? "").replace(/\/+$/, "");
    return prefix.split("/").pop() ?? "";
  };

  engineSelect.addEventListener("change", () => {
    currentEngine = engineSelect.value;
    selected.clear();
    updateSelectionInfo();
    void navigate(engineSubdir(currentEngine), true);
  });

  const finish = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") finish();
  };
  closeBtn.addEventListener("click", finish);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) finish();
  });
  document.addEventListener("keydown", onKey);

  createBtn.addEventListener("click", async () => {
    const paths = [...selected];
    if (paths.length === 0) return;
    const mode = (modeSeed.checked ? "seed" : "download") as CreateMode;
    const skipEpisode = !episodeCheck.checked;
    createBtn.disabled = true;
    progressBox.replaceChildren();
    let anyFailed = false;

    await Promise.all(
      paths.map(async (sourcePath) => {
        const row = el("div", { className: "creator-task" });
        const label = el("span", { className: "creator-task__name" }, [sourcePath]);
        const status = el("span", { className: "creator-task__status" }, ["Постановка в очередь…"]);
        row.append(label, status);
        progressBox.append(row);
        try {
          const created = await fetchJson<CreatorTaskOut>("/creator/tasks", {
            method: "POST",
            body: JSON.stringify({
              engine_id: currentEngine,
              source_path: sourcePath,
              skip_episode_check: skipEpisode,
            }),
          });
          const poll = async () => {
            for (;;) {
              const task = await fetchJson<CreatorTaskOut>(
                `/creator/tasks/${created.engine_id}/${created.id}`,
              );
              status.textContent = `${task.message} (${task.progress}%)`;
              if (TERMINAL_CREATE_STATUSES.has(task.status)) return task;
              await new Promise((r) => setTimeout(r, CREATE_POLL_MS));
            }
          };
          const task = await poll();
          if (task.status !== "completed") {
            anyFailed = true;
            status.textContent = `✗ ${task.message}`;
            row.classList.add("creator-task--fail");
            return;
          }
          if (mode === "download") {
            status.textContent = "✓ Готово";
            const dl = el("button", { type: "button", className: "btn btn--sm btn--primary creator-task__btn" }, [
              icon("download"),
              "Скачать",
            ]);
            dl.addEventListener("click", () => {
              downloadCreatedTorrent(task).catch((e) =>
                showToast(e instanceof Error ? e.message : String(e), true),
              );
            });
            row.append(dl);
          } else {
            await fetchJson<TorrentOut>(`/creator/tasks/${task.engine_id}/${task.id}/seed`, {
              method: "POST",
              body: JSON.stringify({
                label: labelCombo.value(),
                display_name: paths.length === 1 ? nameInput.value.trim() : "",
              }),
            });
            status.textContent = "✓ Создан и поставлен на раздачу";
          }
          row.classList.add("creator-task--ok");
        } catch (e) {
          anyFailed = true;
          status.textContent = `✗ ${e instanceof Error ? e.message : String(e)}`;
          row.classList.add("creator-task--fail");
        }
      }),
    );

    showToast(anyFailed ? "Завершено с ошибками" : "Готово", anyFailed);
    if (mode === "seed") onSeeded();
    createBtn.disabled = false;
    selected.clear();
    updateSelectionInfo();
    void navigate(currentPath);
  });

  dialog.append(
    el("h2", { id: "create-dialog-title", className: "modal-title" }, ["Создать торрент"]),
    field("Диск (движок)", engineSelect),
    breadcrumb,
    listBox,
    el("div", { className: "creator-selection" }, [selectionInfo]),
    modeRow,
    episodeRow,
    seedFields,
    progressBox,
    (() => {
      const actions = el("div", { className: "modal-actions modal-actions--row" });
      actions.append(closeBtn, createBtn);
      return actions;
    })(),
  );
  overlay.append(dialog);
  document.body.append(overlay);

  createBtn.disabled = true;
  void (async () => {
    try {
      engines = await fetchJson<EngineOut[]>("/engines");
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
      return;
    }
    engineSelect.replaceChildren();
    for (const eng of engines) {
      engineSelect.append(el("option", { value: eng.id }, [`${eng.id} (${eng.storage_prefix})`]));
    }
    if (engines.length > 0) {
      currentEngine = engines[0].id;
      engineSelect.value = currentEngine;
      void navigate(engineSubdir(currentEngine), true);
    } else {
      listBox.append(el("div", { className: "creator-empty" }, ["Нет доступных движков"]));
    }
  })();
}

function openCreatorQueueDialog(onSeeded: () => void): void {
  const overlay = el("div", { className: "modal-overlay" });
  const dialog = el("div", {
    className: "modal-dialog modal-dialog--wide",
    role: "dialog",
    "aria-modal": "true",
    "aria-labelledby": "creator-queue-title",
  });

  const listBox = el("div", { className: "creator-queue" });
  const refreshBtn = el("button", { type: "button", className: "btn btn--sm" }, [icon("refresh"), "Обновить"]);
  const closeBtn = el("button", { type: "button", className: "btn btn--ghost" }, ["Закрыть"]);

  let timer: number | null = null;
  let closed = false;

  const finish = () => {
    closed = true;
    if (timer !== null) clearTimeout(timer);
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") finish();
  };
  closeBtn.addEventListener("click", finish);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) finish();
  });
  document.addEventListener("keydown", onKey);

  const statusLabel = (t: CreatorTaskOut): string => {
    if (t.status === "completed") return "✓ Готово";
    if (t.status === "failed") return `✗ ${t.message}`;
    if (t.status === "cancelled") return "Отменено";
    return `${t.message} (${t.progress}%)`;
  };

  const seedTask = async (t: CreatorTaskOut) => {
    try {
      await fetchJson<TorrentOut>(`/creator/tasks/${t.engine_id}/${t.id}/seed`, {
        method: "POST",
        body: JSON.stringify({ label: "", display_name: "" }),
      });
      showToast("Поставлено на раздачу");
      onSeeded();
      void load();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  };

  const cancelTask = async (t: CreatorTaskOut) => {
    try {
      await fetchJson(`/creator/tasks/${t.engine_id}/${t.id}/cancel`, { method: "POST" });
      void load();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  };

  const deleteTask = async (t: CreatorTaskOut) => {
    const active = t.status === "queued" || t.status === "processing";
    const question = active
      ? `Прервать и удалить задачу «${t.name || t.source_path}»?`
      : `Удалить задачу «${t.name || t.source_path}» из очереди?`;
    if (!window.confirm(question)) return;
    try {
      await fetchJson(`/creator/tasks/${t.engine_id}/${t.id}`, { method: "DELETE" });
      void load();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  };

  const render = (tasks: CreatorTaskOut[]) => {
    listBox.replaceChildren();
    if (tasks.length === 0) {
      listBox.append(el("div", { className: "creator-empty" }, ["Очередь пуста"]));
      return;
    }
    for (const t of tasks) {
      const row = el("div", { className: `creator-task creator-task--${t.status}` });
      const info = el("div", { className: "creator-task__info" }, [
        el("span", { className: "creator-task__name" }, [`${t.engine_id}: ${t.name || t.source_path}`]),
        el("span", { className: "creator-task__status" }, [statusLabel(t)]),
      ]);
      const actions = el("div", { className: "creator-task__actions" });
      if (t.status === "queued" || t.status === "processing") {
        const c = el("button", { type: "button", className: "btn btn--sm creator-task__btn" }, [icon("x"), "Отмена"]);
        c.addEventListener("click", () => void cancelTask(t));
        actions.append(c);
      }
      if (t.status === "completed" && t.has_torrent) {
        const dl = el("button", { type: "button", className: "btn btn--sm creator-task__btn" }, [
          icon("download"),
          "Скачать",
        ]);
        dl.addEventListener("click", () =>
          downloadCreatedTorrent(t).catch((e) => showToast(e instanceof Error ? e.message : String(e), true)),
        );
        actions.append(dl);
        const seed = el("button", { type: "button", className: "btn btn--sm btn--primary creator-task__btn" }, [
          icon("play"),
          "Раздать",
        ]);
        seed.addEventListener("click", () => void seedTask(t));
        actions.append(seed);
      }
      const del = el("button", { type: "button", className: "btn btn--sm btn--danger creator-task__btn" }, [
        icon("trash"),
        "Удалить",
      ]);
      del.addEventListener("click", () => void deleteTask(t));
      actions.append(del);
      row.append(info, actions);
      listBox.append(row);
    }
  };

  const load = async () => {
    if (closed) return;
    try {
      const tasks = await fetchJson<CreatorTaskOut[]>("/creator/tasks");
      if (closed) return;
      render(tasks);
      const active = tasks.some((t) => t.status === "queued" || t.status === "processing");
      if (timer !== null) clearTimeout(timer);
      if (active) timer = window.setTimeout(() => void load(), CREATE_POLL_MS);
    } catch (e) {
      if (closed) return;
      listBox.replaceChildren(
        el("div", { className: "creator-empty" }, [e instanceof Error ? e.message : String(e)]),
      );
    }
  };

  refreshBtn.addEventListener("click", () => void load());

  dialog.append(
    el("h2", { id: "creator-queue-title", className: "modal-title" }, ["Очередь создания торрентов"]),
    el("p", { className: "field__hint" }, [
      "Задачи создания на всех движках. Хранятся в памяти движка, автоудаляются через 24 часа " +
        "и очищаются при его перезапуске.",
    ]),
    listBox,
    (() => {
      const bar = el("div", { className: "modal-actions modal-actions--row" });
      bar.append(closeBtn, refreshBtn);
      return bar;
    })(),
  );
  overlay.append(dialog);
  document.body.append(overlay);

  listBox.append(el("div", { className: "creator-empty" }, ["Загрузка…"]));
  void load();
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
  const credsWrap = el("div", { className: "login-creds" }, [
    field("Пользователь", userInput),
    field("Пароль", passInput),
  ]);
  const subText = el("p", { className: "login-sub" }, ["Войдите по имени пользователя и паролю."]);
  const toggleKey = el("button", { type: "button", className: "btn btn--ghost login-alt" }, [
    "Войти по API-ключу",
  ]);
  toggleKey.addEventListener("click", () => {
    const show = keyWrap.hasAttribute("hidden");
    keyWrap.toggleAttribute("hidden", !show);
    credsWrap.toggleAttribute("hidden", show);
    toggleKey.textContent = show ? "Войти по логину и паролю" : "Войти по API-ключу";
    subText.textContent = show ? "Войдите по API-ключу." : "Войдите по имени пользователя и паролю.";
    if (show) keyInput.focus();
    else userInput.focus();
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
      subText,
    ]),
    el("div", { className: "login-form" }, [
      credsWrap,
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

type JobEnqueueOut = { enqueued: boolean; job: string; job_id?: string | null };
type JobResultOut = { job_id: string; status: string; success?: boolean; result?: unknown };

function pollJobResult(jobId: string, timeoutMs = 30000): Promise<JobResultOut | null> {
  // WS (Фаза 7, WS-4): результат джобы пушем через канал job:{id} (бэкенд — ws_pollers следит за
  // arq). Поллинг остаётся фолбэком, если WS-канал молчит. Кто первый отдаст терминальный статус.
  const deadline = Date.now() + timeoutMs;
  return new Promise<JobResultOut | null>((resolve) => {
    let done = false;
    let off: (() => void) | null = null;
    let timer = 0;
    const finish = (r: JobResultOut | null) => {
      if (done) return;
      done = true;
      off?.();
      if (timer) window.clearTimeout(timer);
      resolve(r);
    };
    const consider = (r: JobResultOut | undefined) => {
      if (r && (r.status === "complete" || r.status === "not_found")) finish(r);
    };
    off = wsSubscribe(`job:${jobId}`, (msg) => consider(msg.data as JobResultOut));
    const poll = async () => {
      if (done) return;
      if (Date.now() >= deadline) return finish(null);
      try {
        const r = await fetchJson<JobResultOut>(`/jobs/result/${encodeURIComponent(jobId)}`);
        if (r.status === "complete" || r.status === "not_found") return finish(r);
      } catch {
        // транзиентная ошибка — повторим
      }
      timer = window.setTimeout(() => void poll(), 1500);
    };
    void poll();
  });
}

function formatJobResult(job: string, res: JobResultOut): string {
  if (res.status === "not_found") return "результат недоступен (устарел)";
  if (res.success === false) {
    return `ошибка: ${typeof res.result === "string" ? res.result : JSON.stringify(res.result)}`;
  }
  const r = res.result as Record<string, unknown> | undefined;
  if (!r || typeof r !== "object") return "готово";
  if (job === "check_engine_health") {
    const ids = Object.keys((r.engines ?? {}) as Record<string, unknown>);
    return ids.length ? `движки отвечают: ${ids.length} (${ids.join(", ")})` : "движки не ответили";
  }
  if (job === "sync_runtime_to_db") {
    return (
      `runtime ${r.runtime_total ?? "?"} · в БД ${r.db_total ?? "?"} · ` +
      `обновлено статусов ${r.updated_status ?? 0}, infohash ${r.updated_info_hash ?? 0}; ` +
      `в runtime без БД ${r.runtime_missing_db ?? 0}, в БД без runtime ${r.db_missing_runtime ?? 0}`
    );
  }
  if (job === "restore_engine" || job === "bulk_register_engine") {
    const parts: string[] = [];
    if ("restored" in r) parts.push(`восстановлено ${r.restored}`);
    if ("registered" in r) parts.push(`зарегистрировано ${r.registered}`);
    if ("failed" in r) parts.push(`ошибок ${r.failed}`);
    return parts.join(" · ") || "готово";
  }
  if (job === "restore_all_engines") {
    const engines = (r.engines ?? []) as Array<Record<string, unknown>>;
    const restored = engines.reduce((s, e) => s + (Number(e.restored) || 0), 0);
    const failed = engines.reduce((s, e) => s + (Number(e.failed) || 0), 0);
    return `движков ${engines.length} · восстановлено ${restored} · ошибок ${failed}`;
  }
  return "готово";
}

function mountComponentsPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, [
    "Перезагрузка компонентов",
  ]);
  const refreshBtn = el(
    "button",
    { type: "button", className: "btn btn--ghost btn--sm", title: "Обновить" },
    [icon("refresh")],
  );
  head.append(refreshBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const hint = el("p", { className: "field__hint" }, [
    "Перезапуск контейнеров сервиса. Движки перезапускаются отдельно — в «Реестре движков» ниже.",
  ]);
  const list = el("div", { className: "keys-list" });
  body.append(hint, list);
  panel.append(body);

  const LABELS: Record<string, string> = {
    api: "API",
    db: "PostgreSQL",
    redis: "Redis",
    queue_worker: "Очередь (ARQ)",
  };
  const CONFIRMS: Record<string, string> = {
    api: "Перезапустить API? Веб-интерфейс на пару секунд потеряет связь.",
    db: "Перезапустить PostgreSQL? На время рестарта изменения в БД будут недоступны.",
    redis: "Перезапустить Redis? Очередь задач кратко прервётся.",
    queue_worker: "Перезапустить воркер очереди (ARQ)?",
  };
  const ORDER = ["api", "db", "redis", "queue_worker"];

  const restart = async (service: string, btn: HTMLButtonElement) => {
    if (!window.confirm(CONFIRMS[service] ?? `Перезапустить «${service}»?`)) return;
    btn.disabled = true;
    try {
      const r = await fetchJson<{ message?: string }>(
        `/components/${encodeURIComponent(service)}/restart`,
        { method: "POST" },
      );
      showToast(r.message ?? `${LABELS[service] ?? service}: перезапущен`);
      setTimeout(() => void reload(), 2000);
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      btn.disabled = false;
    }
  };

  const reload = async () => {
    try {
      const data = await fetchJson<ComponentsOut>("/components");
      list.replaceChildren();
      if (!data.available) {
        list.append(el("p", { className: "field__hint" }, [data.reason ?? "Перезапуск недоступен"]));
        return;
      }
      const byService = new Map((data.components ?? []).map((c) => [c.service, c]));
      for (const svc of ORDER) {
        const c = byService.get(svc);
        const running = c?.state === "running";
        const row = el("div", { className: `key-row${running ? "" : " key-row--off"}` });
        const meta = el("div", { className: "key-row__meta" }, [
          el("span", { className: "key-row__name" }, [LABELS[svc] ?? svc]),
          el("span", { className: "key-row__sub" }, [c?.status ?? "контейнер не найден"]),
        ]);
        const btn = el("button", { type: "button", className: "btn btn--sm btn--danger" }, [
          "Перезапустить",
        ]) as HTMLButtonElement;
        if (!c) btn.disabled = true;
        btn.addEventListener("click", () => void restart(svc, btn));
        row.append(meta, el("div", { className: "btn-row" }, [btn]));
        list.append(row);
      }
    } catch (e) {
      list.replaceChildren(
        el("p", { className: "field__hint" }, [e instanceof Error ? e.message : String(e)]),
      );
    }
  };

  refreshBtn.addEventListener("click", () => void reload());
  void reload();
  return panel;
}

function mountMaintenancePanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["Очередь задач"]));
  const body = el("div", { className: "panel__body" });
  body.append(
    el("p", { className: "field__hint" }, [
      "Ручной запуск фоновых задач (очередь ARQ). Восстановление перечитывает раздачи из БД " +
        "и заново заводит их на движках — полезно после сбоя или потери сессии движка.",
    ]),
  );

  const out = el("div", { className: "field__hint job-result" }, [""]);

  const run = async (path: string, label: string, btn: HTMLButtonElement) => {
    btn.disabled = true;
    out.className = "field__hint job-result";
    out.textContent = `${label}: выполняется…`;
    try {
      const enq = await fetchJson<JobEnqueueOut>(path, { method: "POST" });
      if (!enq.job_id) {
        out.textContent = `${label}: задача поставлена в очередь`;
        return;
      }
      const res = await pollJobResult(enq.job_id);
      if (!res) {
        out.textContent = `${label}: выполняется в фоне (результат не дождались)`;
        return;
      }
      out.textContent = `${label}: ${formatJobResult(enq.job, res)}`;
      out.className =
        res.success === false || res.status === "not_found"
          ? "field__hint job-result conn-bad"
          : "field__hint job-result conn-ok";
    } catch (e) {
      out.textContent = "";
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      btn.disabled = false;
    }
  };

  const restoreAll = el("button", { type: "button", className: "btn btn--sm btn--primary" }, [
    "Восстановить все движки",
  ]);
  restoreAll.addEventListener("click", () => {
    if (!window.confirm("Запустить восстановление всех раздач из БД на всех движках?")) return;
    void run("/jobs/restore-all", "Восстановление всех движков", restoreAll);
  });

  const syncRuntime = el("button", { type: "button", className: "btn btn--sm" }, [
    "Сверить runtime с БД",
  ]);
  syncRuntime.addEventListener("click", () => void run("/jobs/sync-runtime", "Сверка runtime", syncRuntime));

  const healthCheck = el("button", { type: "button", className: "btn btn--sm" }, [
    "Проверить здоровье движков",
  ]);
  healthCheck.addEventListener("click", () =>
    void run("/jobs/engine-health-check", "Проверка движков", healthCheck),
  );

  body.append(el("div", { className: "btn-row" }, [restoreAll, syncRuntime, healthCheck]), out);
  panel.append(body);
  return panel;
}

function fmtUptime(s: number | null | undefined): string {
  if (s == null || s < 0) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}д ${h}ч`;
  if (h > 0) return `${h}ч ${m}м`;
  if (m > 0) return `${m}м`;
  return `${s}с`;
}

function buildEngineDetail(info: EngineInfoOut): HTMLElement {
  const wrap = el("div", { className: "engine-detail__grid" });
  const reg = info.registry;
  const sys = info.sysinfo;
  const conn = info.connectivity;
  const ses = info.session;

  const kv = (label: string, value: string | null | undefined, cls?: string) =>
    el("div", { className: "engine-detail__kv" }, [
      el("span", { className: "engine-detail__k" }, [label]),
      el("span", { className: `engine-detail__v${cls ? ` ${cls}` : ""}` }, [value && value !== "" ? value : "—"]),
    ]);

  const section = (title: string, rows: HTMLElement[]) =>
    el("div", { className: "engine-detail__sec" }, [
      el("div", { className: "engine-detail__title" }, [title]),
      ...rows,
    ]);

  const memUsed =
    sys && sys.mem_total != null && sys.mem_available != null ? sys.mem_total - sys.mem_available : null;
  const diskUsed =
    sys && sys.disk_total != null && sys.disk_free != null ? sys.disk_total - sys.disk_free : null;
  const load =
    sys && sys.load1 != null ? `${sys.load1} / ${sys.load5 ?? "—"} / ${sys.load15 ?? "—"}` : null;

  wrap.append(
    section("Расположение", [
      kv("URL оркестратора", reg.url),
      kv("LAN/адрес", reg.advertise_host),
      kv("Контейнер/хост", sys?.hostname),
      kv("ОС", sys?.os),
      kv("libtorrent", sys?.libtorrent ?? sys?.backend ?? null),
      kv(
        "Версия движка",
        sys?.version ? `v${sys.version}${sys.built_at ? ` · ${fmtBuildTime(sys.built_at)}` : ""}` : null,
      ),
      kv("Аптайм", fmtUptime(sys?.uptime_seconds)),
      kv("Источник", `${reg.source}${reg.in_pool ? " · в пуле" : ""}${reg.stale ? " · протух" : ""}`),
    ]),
    section("Сеть", [
      kv("Локальный IP", sys?.local_ip),
      kv("Внешний (WAN) IP", sys?.wan_ip, "engine-detail__v--mono"),
      kv("TLS", reg.tls ? "да" : "нет"),
      kv(
        "API",
        conn?.reachable ? `доступен · ${conn.api_latency_ms ?? "?"} мс` : `недоступен${conn?.error ? ` (${conn.error})` : ""}`,
        conn?.reachable ? "conn-ok" : "conn-bad",
      ),
      kv(
        "BT-порт",
        conn
          ? `${conn.bt_port ?? "?"} · ${conn.bt_listening ? "слушает" : "не слушает"}${conn.bt_reachable_hint ? " · входящие ✓" : ""}`
          : null,
      ),
      kv("DHT-узлы", conn?.bt?.dht_nodes != null ? String(conn.bt.dht_nodes) : null),
      kv("Слушает интерфейсы", sys?.listen_interfaces),
    ]),
    section("Нагрузка", [
      kv("CPU", sys?.cpu_pct != null ? `${sys.cpu_pct}% · ${sys.cpu_count ?? "?"} ядер` : null),
      kv("Load avg (1/5/15)", load),
      kv("RAM хоста", memUsed != null ? `${fmtBytes(memUsed)} / ${fmtBytes(sys?.mem_total)}` : null),
      kv("RSS процесса", fmtBytes(sys?.proc_rss)),
      kv(
        "Диск раздачи",
        diskUsed != null ? `${fmtBytes(diskUsed)} / ${fmtBytes(sys?.disk_total)} · своб. ${fmtBytes(sys?.disk_free)}` : null,
      ),
      kv("Путь раздачи", sys?.storage_path ?? reg.storage_prefix ?? sys?.data_root),
    ]),
    section("Раздачи", [
      kv("Всего", ses?.torrents != null ? String(ses.torrents) : null),
      kv("Активных", ses?.torrents_active != null ? String(ses.torrents_active) : null),
      kv("Отдача / приём", ses ? `${fmtRate(ses.upload_rate)} / ${fmtRate(ses.download_rate)}` : null),
      kv("Роздано всего", fmtBytes(ses?.total_uploaded)),
      kv("Скачано всего", fmtBytes(ses?.total_downloaded)),
      kv("Пиры / сиды", ses ? `${ses.peers ?? 0} / ${ses.seeds ?? 0}` : null),
    ]),
  );
  return wrap;
}

function showEngineDetailModal(engineId: string, engineName: string): void {
  const overlay = el("div", { className: "modal-overlay" });
  const dialog = el("div", {
    className: "modal-dialog modal-dialog--wide",
    role: "dialog",
    "aria-modal": "true",
    "aria-label": `Движок ${engineName}`,
  });
  const closeBtn = el(
    "button",
    { type: "button", className: "btn btn--ghost btn--sm", "aria-label": "Закрыть" },
    ["✕"],
  ) as HTMLButtonElement;
  const head = el("div", { className: "engine-modal__head" }, [
    el("h2", { className: "modal-title" }, [`Движок ${engineName}`]),
    closeBtn,
  ]);
  const bodyEl = el("div", { className: "engine-modal__body" }, [
    el("p", { className: "field__hint" }, ["Загрузка…"]),
  ]);
  dialog.append(head, bodyEl);
  overlay.append(dialog);

  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") close();
  };
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  document.addEventListener("keydown", onKey);
  document.body.append(overlay);
  closeBtn.focus();

  void (async () => {
    try {
      const info = await fetchJson<EngineInfoOut>(`/engines/${encodeURIComponent(engineId)}/info`);
      bodyEl.replaceChildren(buildEngineDetail(info));
    } catch (e) {
      bodyEl.replaceChildren(
        el("p", { className: "field__hint conn-bad" }, [e instanceof Error ? e.message : String(e)]),
      );
    }
  })();
}

function mountEngineRegistryPanel(): HTMLElement {
  const panel = el("section", { className: "panel" });
  const head = el("div", { className: "panel__head panel__head--with-action" }, ["Реестр движков"]);
  const refreshBtn = el("button", { type: "button", className: "btn btn--ghost btn--sm", title: "Обновить" }, [
    icon("refresh"),
  ]);
  head.append(refreshBtn);
  panel.append(head);

  const body = el("div", { className: "panel__body" });
  const hint = el("p", { className: "field__hint" }, [
    "Все известные движки (статические + саморегистрирующиеся), их доступность и время последнего отклика.",
  ]);
  const list = el("div", { className: "keys-list" });
  body.append(hint, list);
  panel.append(body);

  const fmtAge = (s: number | null) => {
    if (s == null) return "никогда";
    if (s < 60) return `${s} с назад`;
    if (s < 3600) return `${Math.floor(s / 60)} мин назад`;
    if (s < 86400) return `${Math.floor(s / 3600)} ч назад`;
    return `${Math.floor(s / 86400)} дн назад`;
  };

  const probe = async (id: string, btn: HTMLButtonElement, out: HTMLElement) => {
    btn.disabled = true;
    out.textContent = "проверка…";
    try {
      const r = await fetchJson<ConnectivityOut>(`/engines/${encodeURIComponent(id)}/connectivity`);
      if (!r.reachable) {
        out.textContent = `API недоступен: ${r.error ?? "нет ответа"}`;
        out.className = "key-row__sub conn-bad";
      } else {
        const bt = r.bt_listening ? `BT ${r.bt_port ?? "?"} слушает` : "BT не слушает";
        const inc = r.bt_reachable_hint ? " · входящие ✓" : "";
        out.textContent = `API ✓ ${r.api_latency_ms} мс · ${bt}${inc}`;
        out.className = "key-row__sub conn-ok";
      }
    } catch (e) {
      out.textContent = e instanceof Error ? e.message : String(e);
      out.className = "key-row__sub conn-bad";
    } finally {
      btn.disabled = false;
    }
  };

  const jobBtn = (label: string, path: string, confirmMsg?: string) => {
    const b = el("button", { type: "button", className: "btn btn--sm" }, [label]) as HTMLButtonElement;
    b.addEventListener("click", async () => {
      if (confirmMsg && !window.confirm(confirmMsg)) return;
      b.disabled = true;
      try {
        const enq = await fetchJson<JobEnqueueOut>(path, { method: "POST" });
        if (!enq.job_id) {
          showToast(`${label}: уже выполняется`);
          return;
        }
        const res = await pollJobResult(enq.job_id);
        if (res) showToast(`${label}: ${formatJobResult(enq.job, res)}`, res.success === false);
        else showToast(`${label}: выполняется в фоне`);
      } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), true);
      } finally {
        b.disabled = false;
      }
    });
    return b;
  };

  const reload = async () => {
    try {
      const rows = await fetchJson<EngineRegistryItem[]>("/engines/registry");
      list.replaceChildren();
      if (rows.length === 0) {
        list.append(el("p", { className: "field__hint" }, ["Движков нет"]));
        return;
      }
      for (const e of rows) {
        const offline = e.stale || !e.in_pool;
        const row = el("div", { className: `key-row${offline ? " key-row--off" : ""}` });
        const tags: string[] = [e.source];
        if (e.in_pool) tags.push("в пуле");
        if (e.stale) tags.push("протух");
        if (!e.enabled) tags.push("выключен");
        const meta = el("div", { className: "key-row__meta" }, [
          el("span", { className: "key-row__name" }, [
            e.id,
            el("span", { className: "key-row__tag" }, [e.url.startsWith("https") ? "TLS" : "—"]),
          ]),
          el("span", { className: "key-row__sub" }, [
            `${tags.join(" · ")} · отклик ${fmtAge(e.age_seconds)}`,
          ]),
        ]);
        const connOut = el("span", { className: "key-row__sub" }, [""]);
        const probeBtn = el("button", { type: "button", className: "btn btn--sm" }, [
          "Проверить связь",
        ]) as HTMLButtonElement;
        probeBtn.addEventListener("click", () => void probe(e.id, probeBtn, connOut));
        const restoreBtn = jobBtn(
          "Восстановить",
          `/jobs/restore-engine/${encodeURIComponent(e.id)}`,
          `Восстановить раздачи движка «${e.id}» из БД?`,
        );
        const registerBtn = jobBtn(
          "Дорегистрировать",
          `/jobs/bulk-register/${encodeURIComponent(e.id)}`,
        );
        const restartBtn = el("button", { type: "button", className: "btn btn--sm btn--danger" }, [
          "Перезапустить",
        ]) as HTMLButtonElement;
        restartBtn.addEventListener("click", async () => {
          if (!window.confirm(`Перезапустить контейнер движка «${e.id}»?`)) return;
          restartBtn.disabled = true;
          try {
            await fetchJson(`/components/${encodeURIComponent(`engine-${e.id}`)}/restart`, {
              method: "POST",
            });
            showToast(`Движок ${e.id}: перезапущен`);
            setTimeout(() => void reload(), 2000);
          } catch (err) {
            showToast(err instanceof Error ? err.message : String(err), true);
          } finally {
            restartBtn.disabled = false;
          }
        });
        row.append(
          meta,
          el("div", { className: "btn-row" }, [probeBtn, restoreBtn, registerBtn, restartBtn]),
          connOut,
        );
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
        const dlBtn = el("button", { type: "button", className: "btn btn--sm" }, ["Скачать"]);
        dlBtn.addEventListener("click", () => void doDownload(b, dlBtn));
        const restoreBtn = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["Восстановить"]);
        restoreBtn.addEventListener("click", () => void doRestore(b, restoreBtn));
        const delBtn = el("button", { type: "button", className: "btn btn--sm btn--danger" }, ["Удалить"]);
        delBtn.addEventListener("click", () => void doDelete(b, delBtn));
        row.append(el("div", { className: "btn-row" }, [dlBtn, restoreBtn, delBtn]));
        list.append(row);
      }
    } catch (e) {
      hint.textContent = e instanceof Error ? e.message : String(e);
    }
  };

  const doDownload = async (b: BackupItem, btn: HTMLButtonElement) => {
    btn.disabled = true;
    try {
      const res = await fetch(`${API}/backups/${encodeURIComponent(b.filename)}/download`, {
        headers: apiHeaders(false),
      });
      await throwIfNotOk(res);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: b.filename }) as HTMLAnchorElement;
      document.body.append(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      btn.disabled = false;
    }
  };

  const doDelete = async (b: BackupItem, btn: HTMLButtonElement) => {
    if (!window.confirm(`Удалить копию «${b.filename}»? Действие необратимо.`)) return;
    btn.disabled = true;
    try {
      await fetchDelete(`/backups/${encodeURIComponent(b.filename)}`);
      showToast("Копия удалена");
      await reload();
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
      btn.disabled = false;
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
      id: "maint",
      label: "Обслуживание",
      visible: isAdmin(),
      panels: () => [
        mountComponentsPanel(),
        mountMaintenancePanel(),
        mountEngineRegistryPanel(),
        mountBackupsPanel(),
      ],
    },
    {
      id: "logs",
      label: "Логи",
      visible: isAdmin(),
      panels: () => [mountAuditPanel()],
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
    try {
      localStorage.setItem(SETTINGS_TAB_KEY, id);
    } catch {
      /* ignore */
    }
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
  // Широкая раскладка — только для табличного вида списка; на других экранах сбрасываем.
  document.body.classList.remove("layout-wide");
  const route = parseRoute();
  if (route.view === "list") mountListShell(root);
  else if (route.view === "settings") mountSettingsShell(root);
  else mountDetailShell(root, route.id);
  root.append(appFooter());
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
// Кнопки «назад/вперёд» браузера (History API) → перерисовка.
window.addEventListener("popstate", () => render());
// Внутренняя навигация шлёт это событие вручную после pushState (см. pushPath).
window.addEventListener("hashchange", () => render());
void bootstrap();
