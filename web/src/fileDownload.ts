/** Скачивание файла раздачи: ticket → GET на движок (прямо или через RU). */

export type DownloadFeatures = {
  enabled: boolean;
  relay_enabled?: boolean;
};

export type DownloadTicketOut = {
  ticket: string;
  url: string;
  relay_url?: string | null;
  filename: string;
  size: number;
  expires_in: number;
};

export type FileDownloadDeps = {
  fetchJson: <T>(path: string, init?: RequestInit) => Promise<T>;
  showToast: (msg: string, isError?: boolean) => void;
};

const COMPLETE_PROGRESS = 0.999;

let featuresCache: DownloadFeatures | null = null;

export async function loadDownloadFeatures(fetchJson: FileDownloadDeps["fetchJson"]): Promise<DownloadFeatures> {
  if (featuresCache) return featuresCache;
  try {
    featuresCache = await fetchJson<DownloadFeatures>("/download/features");
  } catch {
    featuresCache = { enabled: false, relay_enabled: false };
  }
  return featuresCache;
}

export function fileIsComplete(progress: number | undefined): boolean {
  return (progress ?? 0) >= COMPLETE_PROGRESS;
}

export function downloadHref(ticket: DownloadTicketOut, viaRelay: boolean): string | null {
  const base = viaRelay ? ticket.relay_url : ticket.url;
  if (!base) return null;
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}ticket=${encodeURIComponent(ticket.ticket)}`;
}

export function triggerBrowserDownload(href: string): void {
  const a = document.createElement("a");
  a.href = href;
  a.rel = "noopener";
  a.target = "_blank";
  document.body.append(a);
  a.click();
  a.remove();
}

export async function startFileDownload(
  deps: FileDownloadDeps,
  torrentId: number,
  path: string,
  viaRelay: boolean,
): Promise<void> {
  const ticket = await deps.fetchJson<DownloadTicketOut>("/download/ticket", {
    method: "POST",
    body: JSON.stringify({ torrent_id: torrentId, path }),
  });
  const href = downloadHref(ticket, viaRelay);
  if (!href) {
    deps.showToast(viaRelay ? "Релей не настроен" : "Нет URL скачивания", true);
    return;
  }
  triggerBrowserDownload(href);
}
