/** Тестовая загрузка файлов: ticket → чанки на upload-host → complete. */

export type UploadRoute = "direct" | "relay";

export type UploadFeatures = {
  enabled: boolean;
  relay_enabled?: boolean;
  max_parallel_uploads: number;
  chunk_concurrency: number;
};

type TicketOut = {
  ticket: string;
  upload_base_url: string;
  route?: UploadRoute;
  engine_id: string;
  dest_dir: string;
  filename: string;
  size: number;
  expires_in: number;
};

export type QueuedUpload = {
  key: string;
  file: File;
  engineId: string;
  destDir: string;
  route: UploadRoute;
  status: "queued" | "uploading" | "done" | "error" | "cancelled";
  /** Отдельный флаг: status сужается TS после присвоения "uploading". */
  cancelled: boolean;
  progress: number;
  /** Текущая скорость, байт/с (скользящее окно). */
  speedBps: number;
  error?: string;
  finalPath?: string;
  uploadId?: string;
  ticket?: string;
  uploadBase?: string;
  /** Для расчёта speedBps. */
  _speedAt?: number;
  _speedBytes?: number;
};

const ROUTE_LS_KEY = "ui.uploadRoute";

function loadUploadRoute(): UploadRoute {
  try {
    const v = localStorage.getItem(ROUTE_LS_KEY);
    if (v === "relay" || v === "direct") return v;
  } catch {
    /* ignore */
  }
  return "direct";
}

function saveUploadRoute(route: UploadRoute): void {
  try {
    localStorage.setItem(ROUTE_LS_KEY, route);
  } catch {
    /* ignore */
  }
}

export function uploadRelayEnabled(): boolean {
  return Boolean(featuresCache?.relay_enabled);
}

export type FileUploadDeps = {
  apiHeaders: (json?: boolean) => HeadersInit;
  fetchJson: <T>(path: string, init?: RequestInit) => Promise<T>;
  showToast: (msg: string, isError?: boolean) => void;
  canWrite: () => boolean;
  openCreateTorrent: (engineId: string, dirPath: string) => void;
  el: (tag: string, attrs?: Record<string, unknown>, children?: (string | Node)[]) => HTMLElement;
  icon: (name: string) => HTMLElement;
  field: (label: string, control: HTMLElement, hint?: string) => HTMLElement;
  browseCreator: (
    engineId: string,
    path: string,
  ) => Promise<{ name: string; path: string; is_dir: boolean }[]>;
  listEngines: () => Promise<{ id: string; storage_prefix: string; online?: boolean }[]>;
};

let featuresCache: UploadFeatures | null = null;
const queue: QueuedUpload[] = [];
let activeCount = 0;
let queueUi: HTMLElement | null = null;
let queueOverlay: HTMLElement | null = null;
let deps: FileUploadDeps | null = null;

export async function loadUploadFeatures(d: FileUploadDeps): Promise<UploadFeatures> {
  deps = d;
  try {
    featuresCache = await d.fetchJson<UploadFeatures>("/upload/features");
  } catch {
    featuresCache = {
      enabled: false,
      relay_enabled: false,
      max_parallel_uploads: 4,
      chunk_concurrency: 4,
    };
  }
  return featuresCache;
}

/** Сразу применить лимиты из настроек, без повторного GET /upload/features. */
export function applyUploadLimits(limits: {
  max_parallel_uploads: number;
  chunk_concurrency: number;
}): void {
  if (!featuresCache) {
    featuresCache = {
      enabled: false,
      max_parallel_uploads: limits.max_parallel_uploads,
      chunk_concurrency: limits.chunk_concurrency,
    };
    return;
  }
  featuresCache.max_parallel_uploads = limits.max_parallel_uploads;
  featuresCache.chunk_concurrency = limits.chunk_concurrency;
}

export function uploadFeatureEnabled(): boolean {
  return Boolean(featuresCache?.enabled && deps?.canWrite());
}

/** Входящая скорость заливки файлов (браузер → движок), байт/с, по engine_id. */
export function fileUploadInboundByEngine(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const item of queue) {
    if (item.status !== "uploading" || item.speedBps <= 0) continue;
    out[item.engineId] = (out[item.engineId] ?? 0) + item.speedBps;
  }
  return out;
}

export function fileUploadInboundTotal(): number {
  let n = 0;
  for (const v of Object.values(fileUploadInboundByEngine())) n += v;
  return n;
}

const fileRateListeners = new Set<() => void>();
let fileRateNotifyTimer: number | null = null;

function notifyFileUploadRates(): void {
  if (fileRateNotifyTimer != null) return;
  fileRateNotifyTimer = window.setTimeout(() => {
    fileRateNotifyTimer = null;
    for (const cb of fileRateListeners) cb();
  }, 400);
}

/** Живые скорости очереди «Файл» — для чипа «Скачивание» и экрана сети. */
export function subscribeFileUploadRates(cb: () => void): () => void {
  fileRateListeners.add(cb);
  return () => {
    fileRateListeners.delete(cb);
  };
}

function maxParallel(): number {
  return featuresCache?.max_parallel_uploads ?? 4;
}

function chunkConc(): number {
  return featuresCache?.chunk_concurrency ?? 4;
}

function fmtUploadBytes(v: number): string {
  if (!Number.isFinite(v) || v < 0) return "—";
  if (v === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"] as const;
  let n = v;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  if (i === 0) return `${n} ${units[i]}`;
  return `${n.toFixed(i === 1 ? 0 : 1)} ${units[i]}`;
}

function fmtUploadRate(bps: number): string {
  if (!Number.isFinite(bps) || bps <= 0) return "—";
  const kb = bps / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB/s`;
  return `${(kb / 1024).toFixed(1)} MB/s`;
}

function fmtUploadEta(remainBytes: number, bps: number): string {
  if (!Number.isFinite(bps) || bps <= 0 || remainBytes <= 0) return "";
  const sec = Math.ceil(remainBytes / bps);
  if (sec < 60) return `~${sec} с`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return `~${m}:${String(s).padStart(2, "0")}`;
  const h = Math.floor(m / 60);
  return `~${h} ч ${m % 60} мин`;
}

function noteUploadBytes(item: QueuedUpload, doneBytes: number): void {
  const now = performance.now();
  if (item._speedAt == null || item._speedBytes == null) {
    item._speedAt = now;
    item._speedBytes = doneBytes;
    return;
  }
  const elapsed = (now - item._speedAt) / 1000;
  if (elapsed < 0.3) return;
  const delta = Math.max(0, doneBytes - item._speedBytes);
  const instant = delta / elapsed;
  item.speedBps = item.speedBps > 0 ? item.speedBps * 0.55 + instant * 0.45 : instant;
  item._speedAt = now;
  item._speedBytes = doneBytes;
}

function uploadStatusLabel(status: QueuedUpload["status"]): string {
  switch (status) {
    case "queued":
      return "В очереди";
    case "uploading":
      return "Загрузка";
    case "done":
      return "Готово";
    case "error":
      return "Ошибка";
    case "cancelled":
      return "Отменено";
  }
}

function uploadStatusBadge(status: QueuedUpload["status"]): string {
  switch (status) {
    case "queued":
      return "badge badge--queued";
    case "uploading":
      return "badge badge--downloading";
    case "done":
      return "badge badge--seeding";
    case "error":
      return "badge badge--paused upload-badge--error";
    case "cancelled":
      return "badge badge--paused";
  }
}

function folderLeaf(path: string): string {
  const clean = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return clean.split("/").filter(Boolean).pop() || clean;
}

function renderQueue(): void {
  notifyFileUploadRates();
  if (!queueUi || !deps) return;
  const d = deps;
  queueUi.replaceChildren();
  if (queue.length === 0) {
    queueUi.append(
      d.el("div", { className: "upload-queue__empty creator-empty" }, [
        d.icon("inbox"),
        d.el("div", { className: "upload-queue__empty-title" }, ["Очередь пуста"]),
        d.el("div", { className: "upload-queue__empty-hint" }, [
          "Загрузите файлы через меню «Файл» — прогресс появится здесь.",
        ]),
      ]),
    );
    return;
  }

  const active = queue.filter((q) => q.status === "queued" || q.status === "uploading").length;
  const done = queue.filter((q) => q.status === "done").length;
  const failed = queue.filter((q) => q.status === "error").length;
  const parts = [`${queue.length} ${queue.length === 1 ? "файл" : "файлов"}`];
  if (active) parts.push(`${active} активн.`);
  if (done) parts.push(`${done} готово`);
  if (failed) parts.push(`${failed} с ошибкой`);
  queueUi.append(d.el("div", { className: "upload-queue__summary" }, [parts.join(" · ")]));

  const groups = new Map<string, QueuedUpload[]>();
  for (const item of queue) {
    const g = `${item.engineId}\0${item.destDir}`;
    const list = groups.get(g) ?? [];
    list.push(item);
    groups.set(g, list);
  }

  for (const [, items] of groups) {
    const dir = items[0].destDir;
    const engineId = items[0].engineId;
    const block = d.el("div", { className: "upload-group" });

    const head = d.el("div", { className: "upload-group__head" });
    head.append(
      d.el("span", { className: "badge badge--label upload-group__engine" }, [engineId]),
      d.el("div", { className: "upload-group__titles" }, [
        d.el("div", { className: "upload-group__folder", title: dir }, [folderLeaf(dir)]),
        d.el("div", { className: "upload-group__path", title: dir }, [dir]),
      ]),
    );
    block.append(head);

    const files = d.el("div", { className: "upload-group__files" });
    for (const item of items) {
      const pct = Math.round(Math.min(1, Math.max(0, item.progress)) * 100);
      const sent = Math.round(item.file.size * Math.min(1, Math.max(0, item.progress)));
      const row = d.el("div", { className: `upload-item upload-item--${item.status}` });

      const top = d.el("div", { className: "upload-item__top" });
      top.append(
        d.el("div", { className: "upload-item__name", title: item.file.name }, [item.file.name]),
        d.el("span", { className: uploadStatusBadge(item.status) }, [
          item.status === "uploading" || item.status === "done" ? `${pct}%` : uploadStatusLabel(item.status),
        ]),
      );

      const barWrap = d.el("div", {
        className: "progress upload-item__progress",
        role: "progressbar",
        "aria-valuemin": "0",
        "aria-valuemax": "100",
        "aria-valuenow": String(pct),
        "aria-label": item.file.name,
      });
      const barClass =
        item.status === "done"
          ? "progress__bar progress__bar--complete"
          : item.status === "error"
            ? "progress__bar progress__bar--error"
            : item.status === "cancelled"
              ? "progress__bar progress__bar--muted"
              : "progress__bar";
      const bar = d.el("div", { className: barClass }) as HTMLElement;
      bar.style.width = `${item.status === "queued" ? 0 : pct}%`;
      if (item.status === "uploading") bar.classList.add("progress__bar--pulse");
      barWrap.append(bar);

      const foot = d.el("div", { className: "upload-item__foot" });
      const metaParts: string[] = [];
      const via = item.route === "relay" ? "через RU" : "напрямую";
      if (item.status === "done") {
        metaParts.push(fmtUploadBytes(item.file.size), uploadStatusLabel(item.status), via);
      } else if (item.status === "uploading") {
        metaParts.push(`${fmtUploadBytes(sent)} / ${fmtUploadBytes(item.file.size)}`);
        metaParts.push(fmtUploadRate(item.speedBps));
        const eta = fmtUploadEta(item.file.size - sent, item.speedBps);
        if (eta) metaParts.push(eta);
        metaParts.push(via);
      } else {
        metaParts.push(fmtUploadBytes(item.file.size), uploadStatusLabel(item.status), via);
      }
      foot.append(d.el("div", { className: "upload-item__meta" }, [metaParts.join(" · ")]));
      if (item.status === "uploading" || item.status === "queued") {
        const cancel = d.el(
          "button",
          { type: "button", className: "btn btn--ghost btn--sm upload-item__cancel" },
          [d.icon("x"), "Отмена"],
        );
        cancel.addEventListener("click", () => void cancelItem(item));
        foot.append(cancel);
      }

      row.append(top, barWrap, foot);
      if (item.error) {
        row.append(d.el("div", { className: "upload-item__error" }, [item.error]));
      }
      files.append(row);
    }
    block.append(files);

    const still = items.some((i) => i.status === "queued" || i.status === "uploading");
    const anyDone = items.some((i) => i.status === "done");
    if (anyDone) {
      const actions = d.el("div", { className: "upload-group__actions" });
      const btn = d.el("button", { type: "button", className: "btn btn--sm btn--primary" }, [
        d.icon("file-plus"),
        "Создать торрент на каталог",
      ]);
      btn.addEventListener("click", () => {
        if (still) {
          const ok = confirm("В каталоге ещё идёт загрузка. Всё равно создать торрент?");
          if (!ok) return;
        }
        d.openCreateTorrent(engineId, dir);
      });
      actions.append(btn);
      block.append(actions);
    }
    queueUi.append(block);
  }
}

async function cancelItem(item: QueuedUpload): Promise<void> {
  item.cancelled = true;
  item.status = "cancelled";
  if (item.uploadId && item.ticket && item.uploadBase) {
    try {
      await fetch(`${item.uploadBase}/upload/v1/uploads/${item.uploadId}`, {
        method: "DELETE",
        headers: { "X-Upload-Ticket": item.ticket },
      });
    } catch {
      /* ignore */
    }
  }
  renderQueue();
  pumpQueue();
}

async function runUpload(item: QueuedUpload): Promise<void> {
  const d = deps!;
  item.status = "uploading";
  item.progress = 0;
  item.speedBps = 0;
  item._speedAt = undefined;
  item._speedBytes = undefined;
  renderQueue();
  try {
    const ticket = await d.fetchJson<TicketOut>("/upload/ticket", {
      method: "POST",
      body: JSON.stringify({
        engine_id: item.engineId,
        dest_dir: item.destDir,
        filename: item.file.name,
        size: item.file.size,
        route: item.route,
      }),
    });
    item.ticket = ticket.ticket;
    item.uploadBase = ticket.upload_base_url;
    item.route = ticket.route ?? item.route;

    let overwrite = false;
    const createOnce = async () => {
      const res = await fetch(`${ticket.upload_base_url}/upload/v1/uploads`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Upload-Ticket": ticket.ticket,
        },
        body: JSON.stringify({ overwrite }),
      });
      if (res.status === 409) {
        const detail = await res.text();
        if (!overwrite && detail.toLowerCase().includes("exists")) {
          const ok = confirm(`Файл «${item.file.name}» уже есть. Перезаписать?`);
          if (!ok) throw new Error("отменено: файл существует");
          overwrite = true;
          return createOnce();
        }
        throw new Error(detail || "conflict");
      }
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{
        id: string;
        chunk_size: number;
        chunk_count: number;
        received: number[];
      }>;
    };

    const created = await createOnce();
    item.uploadId = created.id;
    const chunkSize = created.chunk_size;
    const chunkCount = created.chunk_count;
    const have = new Set(created.received);
    let doneBytes = 0;
    for (const idx of have) {
      const start = idx * chunkSize;
      const end = Math.min(item.file.size, start + chunkSize);
      doneBytes += Math.max(0, end - start);
    }
    item.progress = item.file.size ? doneBytes / item.file.size : 1;
    noteUploadBytes(item, doneBytes);
    renderQueue();

    const pending = Array.from({ length: chunkCount }, (_, i) => i).filter((i) => !have.has(i));
    let cursor = 0;
    const nWorkers = Math.max(1, Math.min(chunkConc(), pending.length || 1));
    let lastUi = 0;
    const workers = Array.from({ length: nWorkers }, async () => {
      while (cursor < pending.length) {
        if (item.cancelled) return;
        const my = cursor++;
        const index = pending[my];
        const start = index * chunkSize;
        const end = Math.min(item.file.size, start + chunkSize);
        const blob = item.file.slice(start, end);
        const buf = new Uint8Array(await blob.arrayBuffer());
        const res = await fetch(
          `${ticket.upload_base_url}/upload/v1/uploads/${created.id}/chunks/${index}`,
          {
            method: "PUT",
            headers: { "X-Upload-Ticket": ticket.ticket },
            body: buf,
          },
        );
        if (!res.ok) throw new Error(await res.text());
        doneBytes += buf.byteLength;
        item.progress = item.file.size ? Math.min(1, doneBytes / item.file.size) : 1;
        noteUploadBytes(item, doneBytes);
        const now = performance.now();
        if (now - lastUi > 200) {
          lastUi = now;
          renderQueue();
        }
      }
    });
    await Promise.all(workers);
    if (item.cancelled) return;

    const doneRes = await fetch(
      `${ticket.upload_base_url}/upload/v1/uploads/${created.id}/complete`,
      {
        method: "POST",
        headers: { "X-Upload-Ticket": ticket.ticket },
      },
    );
    if (!doneRes.ok) throw new Error(await doneRes.text());
    const done = (await doneRes.json()) as { path: string };
    item.finalPath = done.path;
    item.status = "done";
    item.progress = 1;
    item.speedBps = 0;
    d.showToast(`Загружено\n${item.file.name}`);
  } catch (e) {
    if (!item.cancelled) {
      item.status = "error";
      item.error = e instanceof Error ? e.message : String(e);
      d.showToast(item.error, true);
    }
  } finally {
    activeCount = Math.max(0, activeCount - 1);
    renderQueue();
    pumpQueue();
  }
}

function pumpQueue(): void {
  while (activeCount < maxParallel()) {
    const next = queue.find((q) => q.status === "queued");
    if (!next) break;
    activeCount += 1;
    void runUpload(next);
  }
}

export function enqueueFiles(
  files: File[],
  engineId: string,
  destDir: string,
  route: UploadRoute = "direct",
): void {
  const useRoute: UploadRoute =
    route === "relay" && uploadRelayEnabled() ? "relay" : "direct";
  for (const file of files) {
    queue.push({
      key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file,
      engineId,
      destDir,
      route: useRoute,
      status: "queued",
      cancelled: false,
      progress: 0,
      speedBps: 0,
    });
  }
  pumpQueue();
  if (deps) openUploadQueueDialog(deps);
  else renderQueue();
}

/** Попап очереди загрузок (как «Очередь создания» у торрентов). */
export function openUploadQueueDialog(d: FileUploadDeps): void {
  deps = d;
  if (queueOverlay) {
    renderQueue();
    return;
  }

  const overlay = d.el("div", { className: "modal-overlay" });
  const dialog = d.el("div", {
    className: "modal-dialog modal-dialog--wide",
    role: "dialog",
    "aria-modal": "true",
    "aria-labelledby": "upload-queue-title",
  });
  const listBox = d.el("div", { className: "upload-queue" });
  const closeBtn = d.el("button", {
    type: "button",
    className: "btn btn--ghost btn--sm modal-close",
    "aria-label": "Закрыть",
  }, ["✕"]);

  const finish = () => {
    queueOverlay = null;
    queueUi = null;
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") finish();
  };
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) finish();
  });
  closeBtn.addEventListener("click", finish);
  document.addEventListener("keydown", onKey);

  const head = d.el("div", { className: "modal-head panel__head--with-action" }, [
    d.el("h2", { id: "upload-queue-title", className: "modal-title" }, ["Очередь загрузок"]),
    closeBtn,
  ]);

  dialog.append(
    head,
    d.el("p", { className: "field__hint upload-queue__intro" }, [
      "Передача чанками на хост движка. Когда файлы лягут в каталог — можно сразу создать торрент.",
    ]),
    listBox,
  );
  overlay.append(dialog);
  document.body.append(overlay);

  queueOverlay = overlay;
  queueUi = listBox;
  renderQueue();
}

function stripFileExt(name: string): string {
  return name.replace(/\.[^.]+$/, "");
}

/** s04e05 → s04 (сезонный каталог / сезонная раздача). */
function seasonizeEpisodeName(name: string): string {
  return name.replace(/([.\-_])s(\d{1,2})e\d{1,3}(?=[.\-_])/gi, "$1s$2");
}

function namesMatchFolder(fileBase: string, folderName: string): boolean {
  const file = seasonizeEpisodeName(fileBase).toLowerCase();
  const dir = folderName.toLowerCase();
  if (!file || !dir) return false;
  if (file === dir) return true;
  // Файл: Show.s04.HD1080p…  каталог: Show.s04.HD1080p… / Show.s04…
  if (file.startsWith(dir + ".") || dir.startsWith(file + ".")) return true;
  return false;
}

type DestTorrent = { id: number; display_name: string; engine_id: string; save_path: string };

export function openFileUploadDialog(d: FileUploadDeps): void {
  deps = d;
  if (!uploadFeatureEnabled()) {
    d.showToast("Загрузка файлов выключена", true);
    return;
  }

  const overlay = d.el("div", { className: "modal-overlay" });
  const dialog = d.el("div", {
    className: "modal-dialog modal-dialog--wide",
    role: "dialog",
    "aria-modal": "true",
  });

  let engines: { id: string; storage_prefix: string; online?: boolean }[] = [];
  let currentEngine = "";
  let currentPath = "";
  let destLocked = false; // выбран каталог (авто или вручную)
  let selectedFiles: File[] = [];

  const fileInput = d.el("input", {
    type: "file",
    multiple: "true",
    className: "file-input",
  }) as HTMLInputElement;
  const matchHost = d.el("div", { className: "update-rows" });
  const browserWrap = d.el("div", { className: "upload-browser", hidden: "" });
  const engineSelect = d.el("select", { className: "list-filter__select" }) as HTMLSelectElement;
  const breadcrumb = d.el("div", { className: "creator-breadcrumb" });
  const listBox = d.el("div", { className: "creator-browser" });
  const pathLbl = d.el("div", { className: "field__hint" }, ["Каталог не выбран"]);
  const startBtn = d.el("button", {
    type: "button",
    className: "btn btn--primary",
  }, ["В очередь"]) as HTMLButtonElement;
  startBtn.disabled = true;

  let uploadRoute: UploadRoute =
    loadUploadRoute() === "relay" && uploadRelayEnabled() ? "relay" : "direct";
  const routeWrap = d.el("div", { className: "upload-route" });
  const routeDirect = d.el(
    "button",
    {
      type: "button",
      className: `upload-route__btn${uploadRoute === "direct" ? " upload-route__btn--on" : ""}`,
    },
    ["Напрямую"],
  );
  const routeRelayAttrs: Record<string, unknown> = {
    type: "button",
    className: `upload-route__btn${uploadRoute === "relay" ? " upload-route__btn--on" : ""}`,
  };
  if (!uploadRelayEnabled()) routeRelayAttrs.disabled = "true";
  const routeRelay = d.el("button", routeRelayAttrs, ["Через RU"]);
  const syncRouteUi = () => {
    routeDirect.classList.toggle("upload-route__btn--on", uploadRoute === "direct");
    routeRelay.classList.toggle("upload-route__btn--on", uploadRoute === "relay");
  };
  routeDirect.addEventListener("click", () => {
    uploadRoute = "direct";
    saveUploadRoute(uploadRoute);
    syncRouteUi();
  });
  routeRelay.addEventListener("click", () => {
    if (!uploadRelayEnabled()) {
      d.showToast("Загрузка через RU не настроена", true);
      return;
    }
    uploadRoute = "relay";
    saveUploadRoute(uploadRoute);
    syncRouteUi();
  });
  routeWrap.append(
    d.el("div", { className: "upload-route__label" }, ["Маршрут"]),
    d.el("div", { className: "upload-route__tabs" }, [routeDirect, routeRelay]),
  );

  const close = () => {
    overlay.remove();
    document.removeEventListener("paste", onPaste);
  };
  const closeBtn = d.el("button", { type: "button", className: "btn btn--ghost btn--sm" }, ["✕"]);
  closeBtn.addEventListener("click", close);

  const engineSubdir = (id: string): string => {
    const prefix = (engines.find((e) => e.id === id)?.storage_prefix ?? "").replace(/\/+$/, "");
    return prefix.split("/").pop() ?? "";
  };

  const absDest = () => {
    const prefix = (engines.find((e) => e.id === currentEngine)?.storage_prefix ?? "")
      .replace(/\\/g, "/")
      .replace(/\/+$/, "");
    const base = engineSubdir(currentEngine);
    const rel = currentPath.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!rel || rel === base) return prefix;
    if (rel.startsWith(base + "/")) return `${prefix}${rel.slice(base.length)}`;
    return `${prefix}/${rel}`.replace(/\/+/g, "/");
  };

  const syncStart = () => {
    startBtn.disabled = !(selectedFiles.length > 0 && destLocked && currentEngine);
  };

  const setDestFromSavePath = (engineId: string, savePath: string) => {
    currentEngine = engineId;
    if ([...engineSelect.options].some((o) => o.value === engineId)) {
      engineSelect.value = engineId;
    }
    const prefix = (engines.find((e) => e.id === engineId)?.storage_prefix ?? "")
      .replace(/\\/g, "/")
      .replace(/\/+$/, "");
    const base = engineSubdir(engineId);
    let rel = savePath.replace(/\\/g, "/").replace(/\/+$/, "");
    if (prefix && (rel === prefix || rel.startsWith(prefix + "/"))) {
      const rest = rel.slice(prefix.length).replace(/^\//, "");
      rel = rest ? `${base}/${rest}` : base;
    }
    currentPath = rel;
    destLocked = true;
    pathLbl.textContent = `Каталог: ${absDest()}`;
    browserWrap.hidden = true;
    syncStart();
  };

  const renderBreadcrumb = () => {
    breadcrumb.replaceChildren();
    const root = d.el("button", { type: "button", className: "creator-crumb" }, ["/ (диск)"]);
    root.addEventListener("click", () => void navigate(""));
    breadcrumb.append(root);
    let acc = "";
    for (const part of currentPath.split("/").filter(Boolean)) {
      acc = acc ? `${acc}/${part}` : part;
      const target = acc;
      breadcrumb.append(document.createTextNode(" / "));
      const crumb = d.el("button", { type: "button", className: "creator-crumb" }, [part]);
      crumb.addEventListener("click", () => void navigate(target));
      breadcrumb.append(crumb);
    }
  };

  const navigate = async (path: string) => {
    currentPath = path;
    pathLbl.textContent = `Каталог: ${absDest()}`;
    destLocked = true;
    syncStart();
    renderBreadcrumb();
    listBox.replaceChildren(d.el("div", { className: "creator-empty" }, ["Загрузка…"]));
    try {
      const items = (await d.browseCreator(currentEngine, currentPath)).filter(
        (it) => it.is_dir && it.name !== ".upload-tmp" && !it.name.startsWith("."),
      );
      listBox.replaceChildren();
      if (items.length === 0) {
        listBox.append(d.el("div", { className: "creator-empty" }, ["Нет подпапок — можно грузить сюда"]));
        return;
      }
      for (const it of items) {
        const row = d.el("div", { className: "creator-row" });
        const nameEl = d.el(
          "button",
          { type: "button", className: "creator-name creator-name--dir" },
          [`📁 ${it.name}`],
        );
        nameEl.addEventListener("click", () => void navigate(it.path));
        row.append(nameEl);
        listBox.append(row);
      }
    } catch (e) {
      listBox.replaceChildren(
        d.el("div", { className: "creator-empty" }, [e instanceof Error ? e.message : String(e)]),
      );
    }
  };

  const openBrowser = async () => {
    browserWrap.hidden = false;
    if (!currentEngine && engines[0]) {
      currentEngine = engines[0].id;
      engineSelect.value = currentEngine;
    }
    if (!currentPath) currentPath = engineSubdir(currentEngine);
    await navigate(currentPath);
  };

  const renderCandCard = (t: DestTorrent, selected: boolean, onPick: () => void): HTMLElement => {
    const btn = d.el("button", {
      type: "button",
      className: `upload-cand${selected ? " upload-cand--on" : ""}`,
    });
    btn.append(
      d.el("div", { className: "upload-cand__name" }, [t.display_name]),
      d.el("div", { className: "upload-cand__meta" }, [`движок ${t.engine_id}`]),
      d.el("div", { className: "upload-cand__path" }, [t.save_path]),
    );
    btn.addEventListener("click", onPick);
    return btn;
  };

  const renderMatch = (files: File[], candidates: DestTorrent[]) => {
    matchHost.replaceChildren();
    const row = d.el("div", { className: "update-row" });
    const names =
      files.length === 1
        ? files[0].name
        : `${files[0].name} (+ ещё ${files.length - 1})`;
    row.append(d.el("div", { className: "update-row__file" }, [names]));
    const target = d.el("div", { className: "update-row__target update-row__target--stack" });
    row.append(target);

    const pickManual = d.el("a", { href: "#", className: "update-row__change" }, [
      "выбрать каталог вручную",
    ]);
    pickManual.addEventListener("click", (ev) => {
      ev.preventDefault();
      void openBrowser();
    });

    if (candidates.length === 1) {
      const t = candidates[0];
      setDestFromSavePath(t.engine_id, t.save_path);
      const change = d.el("a", { href: "#", className: "update-row__change" }, ["изменить"]);
      change.addEventListener("click", (ev) => {
        ev.preventDefault();
        destLocked = false;
        syncStart();
        void openBrowser();
      });
      target.append(
        d.el("span", { className: "badge badge--seeding" }, ["найдена папка"]),
        renderCandCard(t, true, () => undefined),
        change,
      );
    } else if (candidates.length > 1) {
      let chosen = candidates[0];
      setDestFromSavePath(chosen.engine_id, chosen.save_path);
      const list = d.el("div", { className: "upload-cand-list" });
      const redraw = () => {
        list.replaceChildren();
        for (const t of candidates) {
          list.append(
            renderCandCard(t, t.save_path === chosen.save_path && t.engine_id === chosen.engine_id, () => {
              chosen = t;
              setDestFromSavePath(t.engine_id, t.save_path);
              redraw();
            }),
          );
        }
      };
      redraw();
      target.append(
        d.el("span", { className: "badge badge--queued" }, [`папок: ${candidates.length}`]),
        d.el("div", { className: "field__hint" }, ["Выберите папку (полное имя и путь):"]),
        list,
        pickManual,
      );
    } else {
      destLocked = false;
      browserWrap.hidden = true;
      syncStart();

      const suggested = seasonizeEpisodeName(stripFileExt(files[0].name));
      const nameInput = d.el("input", {
        type: "text",
        className: "list-filter__search upload-new__name",
        value: suggested,
        placeholder: "Имя новой папки",
        spellcheck: "false",
      }) as HTMLInputElement;
      const engSelect = d.el("select", {
        className: "list-filter__select upload-new__engine",
      }) as HTMLSelectElement;
      for (const e of engines) {
        const offline = e.online === false;
        const opt = document.createElement("option");
        opt.value = e.id;
        opt.textContent = offline ? `${e.id} (offline)` : e.id;
        opt.disabled = offline;
        engSelect.append(opt);
      }
      const firstOnline = engines.find((e) => e.online !== false) ?? engines[0];
      if (firstOnline) engSelect.value = firstOnline.id;

      const pathPreview = d.el("div", { className: "upload-new__path" }, []);
      const applyNewFolder = (): boolean => {
        const name = nameInput.value.trim().replace(/[/\\]+/g, "").replace(/^\.+/, "");
        const eng = engSelect.value;
        if (!name || !eng) {
          destLocked = false;
          pathPreview.textContent = "Укажите имя папки и движок";
          syncStart();
          return false;
        }
        if (nameInput.value !== name) nameInput.value = name;
        const prefix = (engines.find((e) => e.id === eng)?.storage_prefix ?? "")
          .replace(/\\/g, "/")
          .replace(/\/+$/, "");
        if (!prefix) {
          destLocked = false;
          pathPreview.textContent = "У движка нет storage_prefix";
          syncStart();
          return false;
        }
        const savePath = `${prefix}/${name}`.replace(/\/+/g, "/");
        setDestFromSavePath(eng, savePath);
        pathPreview.textContent = `Будет создана: ${savePath}`;
        return true;
      };

      const useBtn = d.el(
        "button",
        { type: "button", className: "btn btn--sm btn--primary" },
        [d.icon("file-plus"), "Создать папку и использовать"],
      );
      useBtn.addEventListener("click", () => {
        if (applyNewFolder()) d.showToast(`Каталог: ${absDest()}`);
        else d.showToast("Укажите имя папки и движок", true);
      });
      nameInput.addEventListener("input", () => {
        void applyNewFolder();
      });
      engSelect.addEventListener("change", () => {
        void applyNewFolder();
      });

      const form = d.el("div", { className: "upload-new" });
      form.append(
        d.el("div", { className: "field__hint" }, [
          "Папка не найдена. Создайте новую (появится при загрузке) или выберите вручную.",
        ]),
        d.field("Имя папки", nameInput),
        d.field("Движок", engSelect),
        pathPreview,
        d.el("div", { className: "upload-new__actions" }, [useBtn, pickManual]),
      );
      target.append(
        d.el("span", { className: "badge badge--paused" }, ["не найдено"]),
        form,
      );
      nameInput.dispatchEvent(new Event("input"));
    }
    matchHost.append(row);
  };

  const absFromBrowsePath = (engineId: string, browsePath: string): string => {
    const prefix = (engines.find((e) => e.id === engineId)?.storage_prefix ?? "")
      .replace(/\\/g, "/")
      .replace(/\/+$/, "");
    const base = engineSubdir(engineId);
    const rel = browsePath.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!rel || rel === base) return prefix;
    if (rel.startsWith(base + "/")) return `${prefix}${rel.slice(base.length)}`;
    return `${prefix}/${rel}`.replace(/\/+/g, "/");
  };

  const findFolderCandidates = async (filename: string): Promise<DestTorrent[]> => {
    const fileBase = stripFileExt(filename);
    const seasonFile = seasonizeEpisodeName(fileBase).toLowerCase();
    const found: DestTorrent[] = [];
    const seen = new Set<string>();
    await Promise.all(
      engines.map(async (eng) => {
        const root = engineSubdir(eng.id);
        if (!root) return;
        try {
          const items = await d.browseCreator(eng.id, root);
          for (const it of items) {
            if (!it.is_dir || it.name.startsWith(".")) continue;
            if (!namesMatchFolder(fileBase, it.name)) continue;
            const savePath = absFromBrowsePath(eng.id, it.path);
            const key = `${eng.id}\0${savePath}`;
            if (seen.has(key)) continue;
            seen.add(key);
            found.push({
              // Отрицательный id — маркер «с диска», не путать с id раздачи.
              id: -found.length - 1,
              display_name: it.name,
              engine_id: eng.id,
              save_path: savePath,
            });
          }
        } catch {
          /* движок офлайн — пропускаем */
        }
      }),
    );
    // Точное совпадение сезонного имени / качества — выше в списке.
    found.sort((a, b) => {
      const al = a.display_name.toLowerCase();
      const bl = b.display_name.toLowerCase();
      const as = al === seasonFile ? 0 : seasonFile.startsWith(al + ".") ? 1 : 2;
      const bs = bl === seasonFile ? 0 : seasonFile.startsWith(bl + ".") ? 1 : 2;
      return as - bs || al.length - bl.length;
    });
    return found;
  };

  const onFiles = async (files: File[]) => {
    selectedFiles = files;
    destLocked = false;
    browserWrap.hidden = true;
    syncStart();
    matchHost.replaceChildren();
    if (!files.length) {
      pathLbl.textContent = "Каталог не выбран";
      return;
    }
    matchHost.append(
      d.el("p", { className: "field__hint" }, ["Поиск папки на дисках (s04e05 → s04)…"]),
    );
    try {
      // Только папки на диске — раздачи для заливки не нужны.
      const dirs = await findFolderCandidates(files[0].name);
      renderMatch(files, dirs);
    } catch (e) {
      matchHost.replaceChildren(
        d.el("p", { className: "field__hint" }, [e instanceof Error ? e.message : String(e)]),
      );
      void openBrowser();
    }
  };

  fileInput.addEventListener("change", () => {
    void onFiles(Array.from(fileInput.files ?? []));
  });

  const onPaste = (ev: ClipboardEvent) => {
    const items = ev.clipboardData?.files;
    if (!items?.length) return;
    ev.preventDefault();
    const files = Array.from(items);
    // DataTransfer в input не всегда пишется — держим свой список.
    const dt = new DataTransfer();
    for (const f of files) dt.items.add(f);
    fileInput.files = dt.files;
    void onFiles(files);
  };
  document.addEventListener("paste", onPaste);

  startBtn.addEventListener("click", () => {
    if (!selectedFiles.length) {
      d.showToast("Выберите файлы", true);
      return;
    }
    if (!destLocked || !currentEngine) {
      d.showToast("Укажите каталог назначения", true);
      return;
    }
    enqueueFiles(selectedFiles, currentEngine, absDest(), uploadRoute);
    d.showToast(
      `В очередь: ${selectedFiles.length} (${uploadRoute === "relay" ? "через RU" : "напрямую"})`,
    );
    close();
  });

  browserWrap.append(
    d.field("Движок", engineSelect),
    breadcrumb,
    listBox,
    pathLbl,
  );

  dialog.append(
    d.el("div", { className: "panel__head panel__head--with-action" }, ["Загрузить файлы", closeBtn]),
    d.el("div", { className: "panel__body" }, [
      d.el("p", { className: "field__hint update-intro" }, [
        "Выберите или вставьте файлы — найду папку на диске по имени (эпизод s04e05 → сезон s04). " +
          "Если папки нет — предложу создать новую и выбрать движок.",
      ]),
      routeWrap,
      d.field("Файлы", fileInput, "Только файлы; можно Ctrl+V. Папки целиком — позже"),
      matchHost,
      browserWrap,
      d.el("div", { className: "modal-actions" }, [startBtn]),
    ]),
  );
  overlay.append(dialog);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  document.body.append(overlay);

  void (async () => {
    try {
      engines = await d.listEngines();
    } catch (e) {
      d.showToast(e instanceof Error ? e.message : String(e), true);
      return;
    }
    engineSelect.replaceChildren();
    for (const eng of engines) {
      engineSelect.append(
        d.el("option", { value: eng.id }, [
          `${eng.id}${eng.online === false ? " (офлайн)" : ""}`,
        ]),
      );
    }
    engineSelect.addEventListener("change", () => {
      currentEngine = engineSelect.value;
      void navigate(engineSubdir(currentEngine));
    });
    if (engines[0]) {
      currentEngine = engines[0].id;
      engineSelect.value = currentEngine;
    }
  })();
}
