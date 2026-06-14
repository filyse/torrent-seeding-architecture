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
  status: string;
  created_at: string;
  runtime?: RuntimeOut | null;
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
type Route = { view: "list" } | { view: "detail"; id: number };
type DeleteTorrentChoice = "cancel" | "torrent_only" | "torrent_and_files";

let listPollTimer: ReturnType<typeof setTimeout> | null = null;
let detailPollTimer: ReturnType<typeof setTimeout> | null = null;
let listAbort: AbortController | null = null;
let detailAbort: AbortController | null = null;
let listLoadGeneration = 0;
let lastListItems: TorrentOut[] = [];
let toastTimer: ReturnType<typeof setTimeout> | null = null;

type DetailSpoilerKey = "peers" | "meta";
const detailSpoilerOpenById = new Map<number, Record<DetailSpoilerKey, boolean>>();

function getDetailSpoilerState(torrentId: number): Record<DetailSpoilerKey, boolean> {
  return detailSpoilerOpenById.get(torrentId) ?? { peers: false, meta: false };
}

function saveDetailSpoilerStateFromDom(container: HTMLElement, torrentId: number): void {
  const cur = getDetailSpoilerState(torrentId);
  const peers = container.querySelector('details[data-spoiler="peers"]') as HTMLDetailsElement | null;
  const meta = container.querySelector('details[data-spoiler="meta"]') as HTMLDetailsElement | null;
  detailSpoilerOpenById.set(torrentId, {
    peers: peers?.open ?? cur.peers,
    meta: meta?.open ?? cur.meta,
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

function clearViewPolls(): void {
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
  const m = /^#\/torrent\/(\d+)$/.exec(window.location.hash || "");
  if (m) return { view: "detail", id: Number(m[1]) };
  return { view: "list" };
}

function setHashList(): void {
  window.location.hash = "";
}

function setHashDetail(id: number): void {
  window.location.hash = `#/torrent/${id}`;
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

function renderTorrentCard(t: TorrentOut, onChange: () => void): HTMLElement {
  const progress = t.runtime?.progress ?? 0;
  const pct = Math.round(progress * 1000) / 10;
  const card = el("li", { className: "torrent-card" });
  const title = el(
    "h3",
    { className: "torrent-card__title" },
    [
      (() => {
        const a = el("a", { href: `#/torrent/${t.id}` }, [t.display_name || `Торрент #${t.id}`]);
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
  stats.append(
    document.createTextNode(`${fmtPercent(t.runtime?.progress)} · `),
    el("strong", {}, [`↓ ${fmtRate(t.runtime?.download_rate)}`]),
    document.createTextNode(" · "),
    el("strong", {}, [`↑ ${fmtRate(t.runtime?.upload_rate)}`]),
    document.createTextNode(` · ${t.runtime?.peers ?? 0} пир.`),
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
  card.append(
    el("div", { className: "torrent-card__top" }, [title, badge]),
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
  countEl.textContent =
    items.length === 0
      ? "Нет торрентов"
      : `${items.length} ${items.length === 1 ? "торрент" : items.length < 5 ? "торрента" : "торрентов"}`;
  updateLiveMeta(metaEl, items);
  listEl.replaceChildren();
  if (items.length === 0) {
    listEl.append(
      el("div", { className: "empty-state" }, [
        el("p", {}, ["Пока пусто"]),
        el("p", {}, ["Добавьте magnet или .torrent ниже"]),
      ]),
    );
    return;
  }
  const ul = el("ul", { className: "torrent-list" });
  const refresh = () => void loadTorrents(refs.listEl, refs.countEl, refs.metaEl, { silent: true, scheduleNext: refs.scheduleNext });
  for (const t of items) ul.append(renderTorrentCard(t, refresh));
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

function mountAddPanel(savePathDefault: string, onAdded: (created?: TorrentOut) => void): HTMLElement {
  const panel = el("section", { className: "panel" });
  panel.append(el("div", { className: "panel__head" }, ["Добавить торрент"]));
  const body = el("div", { className: "panel__body" });

  const tabs = el("div", { className: "tabs" });
  const tabMagnet = el("button", { type: "button", className: "tab tab--active", "data-tab": "magnet" }, [
    "Magnet",
  ]);
  const tabFile = el("button", { type: "button", className: "tab", "data-tab": "file" }, ["Файл"]);
  tabs.append(tabMagnet, tabFile);

  const magnetPanel = el("div", { className: "tab-panel", "data-panel": "magnet" });
  const filePanel = el("div", { className: "tab-panel", "data-panel": "file", hidden: "" });

  const magnetInput = el("input", {
    type: "text",
    placeholder: "magnet:?xt=urn:btih:…",
  }) as HTMLInputElement;
  const savePathInput = el("input", {
    type: "text",
    value: savePathDefault,
  }) as HTMLInputElement;
  const nameMagnet = el("input", { type: "text", placeholder: "Название (необязательно)" }) as HTMLInputElement;
  const torrentFile = el("input", { type: "file", accept: ".torrent" }) as HTMLInputElement;
  const nameFile = el("input", { type: "text", placeholder: "Название (необязательно)" }) as HTMLInputElement;

  const switchTab = (name: "magnet" | "file") => {
    const magnet = name === "magnet";
    tabMagnet.classList.toggle("tab--active", magnet);
    tabFile.classList.toggle("tab--active", !magnet);
    magnetPanel.hidden = !magnet;
    filePanel.hidden = magnet;
  };
  tabMagnet.addEventListener("click", () => switchTab("magnet"));
  tabFile.addEventListener("click", () => switchTab("file"));

  magnetPanel.append(
    field("Magnet-ссылка", magnetInput),
    field("Название", nameMagnet),
    el("div", { className: "btn-row" }, [
      el("button", { type: "button", className: "btn btn--primary", id: "btn-add-magnet" }, ["Добавить"]),
    ]),
  );

  filePanel.append(
    field("Файл .torrent", torrentFile),
    field("Название", nameFile),
    el("div", { className: "btn-row" }, [
      el("button", { type: "button", className: "btn btn--primary", id: "btn-add-file" }, ["Загрузить"]),
    ]),
  );

  body.append(field("Папка на сервере", savePathInput, "Обычно /data в Docker"), tabs, magnetPanel, filePanel);

  magnetPanel.querySelector("#btn-add-magnet")?.addEventListener("click", async () => {
    const magnet_uri = magnetInput.value.trim();
    const save_path = savePathInput.value.trim();
    if (!magnet_uri || !save_path) {
      showToast("Укажите magnet и папку", true);
      return;
    }
    try {
      const created = await fetchJson<TorrentOut>("/torrents", {
        method: "POST",
        body: JSON.stringify({
          magnet_uri,
          save_path,
          display_name: nameMagnet.value.trim(),
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

  filePanel.querySelector("#btn-add-file")?.addEventListener("click", async () => {
    const save_path = savePathInput.value.trim();
    const file = torrentFile.files?.[0];
    if (!file || !save_path) {
      showToast("Выберите файл и папку", true);
      return;
    }
    try {
      const body = new FormData();
      body.set("torrent_file", file, file.name);
      body.set("save_path", save_path);
      body.set("display_name", nameFile.value.trim());
      const res = await fetch(`${API}/torrents/upload`, { method: "POST", headers: apiHeaders(false), body });
      await throwIfNotOk(res);
      const created = (await res.json()) as TorrentOut;
      torrentFile.value = "";
      nameFile.value = "";
      showToast("Торрент загружен");
      onAdded(created);
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    }
  });

  panel.append(body);
  return panel;
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

  const onAdded = (created?: TorrentOut) => {
    if (created) showTorrentInList(listRefs, created);
    void refresh({ afterAdd: true });
  };

  const header = el("header", { className: "app-header" }, [
    el("div", {}, [el("h1", {}, ["Раздача"]), el("p", { className: "field__hint" }, ["Управление торрентами"])]),
    metaEl,
  ]);

  const toolbar = el("div", { className: "list-toolbar" }, [
    countEl,
    el("button", { type: "button", className: "btn btn--ghost btn--sm", id: "btn-refresh" }, ["Обновить"]),
  ]);
  toolbar.querySelector("#btn-refresh")?.addEventListener("click", () => void refresh());

  root.append(
    header,
    mountAddPanel("/data", onAdded),
    toolbar,
    listHost,
  );

  const onVisibility = () => {
    if (parseRoute().view !== "list") return;
    if (!document.hidden) void refresh();
    else if (listPollTimer !== null) {
      clearTimeout(listPollTimer);
      listPollTimer = null;
    }
  };
  document.addEventListener("visibilitychange", onVisibility);

  void refresh();
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
    addStat("Скачивание", fmtRate(data.runtime?.download_rate));
    addStat("Отдача", fmtRate(data.runtime?.upload_rate));
    addStat("Отдано всего", fmtBytes(data.runtime?.total_uploaded));
    addStat("Пиры", String(data.runtime?.peers ?? "—"));
    addStat("Папка", data.save_path);
    addStat("Статус", displayStatusLabel(data));

    const actions = el("div", { className: "btn-row" });
    const pauseBtn = el("button", { type: "button", className: "btn" }, ["Пауза"]);
    const resumeBtn = el("button", { type: "button", className: "btn btn--primary" }, ["Старт"]);
    const delBtn = el("button", { type: "button", className: "btn btn--danger" }, ["Удалить"]);
    const backRefresh = () => loadDetail(id, container, metaEl, scheduleNext);

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
    actions.append(pauseBtn, resumeBtn, delBtn);

    body.append(
      el("span", { className: badgeClass(effectiveStatus(data)) }, [displayStatusLabel(data)]),
      el("h1", {}, [data.display_name || `Торрент #${data.id}`]),
      bar,
      grid,
      actions,
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

function render(): void {
  clearViewPolls();
  const root = document.getElementById("app");
  if (!root) return;
  root.replaceChildren();
  const route = parseRoute();
  if (route.view === "list") mountListShell(root);
  else mountDetailShell(root, route.id);
}

document.title = "Раздача";
window.addEventListener("hashchange", () => render());
render();
