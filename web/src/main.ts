import "./style.css";

const API = "/api/v1";

type TorrentOut = {
  id: number;
  info_hash: string | null;
  magnet_uri: string | null;
  display_name: string;
  save_path: string;
  status: string;
  created_at: string;
};

type TorrentDetailOut = TorrentOut & {
  runtime: Record<string, unknown> | null;
};

type Route = { view: "list" } | { view: "detail"; id: number };

let listPollTimer: ReturnType<typeof setInterval> | null = null;
let detailPollTimer: ReturnType<typeof setInterval> | null = null;

function clearViewPolls(): void {
  if (listPollTimer !== null) {
    clearInterval(listPollTimer);
    listPollTimer = null;
  }
  if (detailPollTimer !== null) {
    clearInterval(detailPollTimer);
    detailPollTimer = null;
  }
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
      throw new Error("401: требуется авторизация (задайте localStorage.seedingApiKey)");
    }
    if (res.status === 403) {
      throw new Error("403: доступ запрещён");
    }
    const text = await res.text();
    let detail = text || res.statusText;
    try {
      const body = JSON.parse(text) as {
        detail?: unknown;
        error?: { message?: string };
      };
      if (body.error?.message !== undefined) {
        detail = String(body.error.message);
      } else if (body.detail !== undefined) {
        detail = JSON.stringify(body.detail);
      }
    } catch {
      /* оставляем detail как текст */
    }
    throw new Error(`${res.status}: ${detail}`);
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

async function fetchDelete(path: string): Promise<void> {
  const res = await fetch(`${API}${path}`, {
    method: "DELETE",
    headers: { ...apiHeaders(false), ...{} },
  });
  await throwIfNotOk(res);
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
  const h = window.location.hash || "";
  const m = /^#\/torrent\/(\d+)$/.exec(h);
  if (m) return { view: "detail", id: Number(m[1]) };
  return { view: "list" };
}

function setHashList(): void {
  window.location.hash = "";
}

function setHashDetail(id: number): void {
  window.location.hash = `#/torrent/${id}`;
}

function formatMagnet(m: string | null): string {
  if (!m) return "—";
  return m.length > 64 ? `${m.slice(0, 64)}…` : m;
}

async function loadTorrents(tableBody: HTMLElement, status: HTMLElement): Promise<void> {
  status.textContent = "Загрузка…";
  try {
    const items = await fetchJson<TorrentOut[]>("/torrents");
    tableBody.replaceChildren();
    if (items.length === 0) {
      const tr = el("tr");
      const td = el("td", { colspan: "6" });
      td.append(document.createTextNode("Нет торрентов."));
      tr.append(td);
      tableBody.append(tr);
    } else {
      for (const t of items) {
        const tr = el("tr");
        const idLink = el("a", { href: `#/torrent/${t.id}` }, [String(t.id)]);
        idLink.addEventListener("click", (ev) => {
          ev.preventDefault();
          setHashDetail(t.id);
          window.dispatchEvent(new HashChangeEvent("hashchange"));
        });
        tr.append(
          el("td", {}, [idLink]),
          el("td", {}, [t.display_name || "—"]),
          el("td", {}, [el("code", {}, [formatMagnet(t.magnet_uri)])]),
          el("td", {}, [t.save_path]),
          el("td", {}, [t.status]),
          el("td", {}, [
            (() => {
              const wrap = el("span", { className: "row-actions" });
              const pauseBtn = el("button", { type: "button" }, ["Пауза"]);
              pauseBtn.addEventListener("click", async () => {
                try {
                  await fetchJson<TorrentOut>(`/torrents/${t.id}/pause`, { method: "POST" });
                  await loadTorrents(tableBody, status);
                } catch (e) {
                  status.textContent = e instanceof Error ? e.message : String(e);
                }
              });
              const resumeBtn = el("button", { type: "button" }, ["Старт"]);
              resumeBtn.addEventListener("click", async () => {
                try {
                  await fetchJson<TorrentOut>(`/torrents/${t.id}/resume`, { method: "POST" });
                  await loadTorrents(tableBody, status);
                } catch (e) {
                  status.textContent = e instanceof Error ? e.message : String(e);
                }
              });
              const delBtn = el("button", { type: "button", className: "btn-danger" }, ["Удалить"]);
              delBtn.addEventListener("click", async () => {
                if (!confirm(`Удалить торрент #${t.id} из БД и движка?`)) return;
                try {
                  await fetchDelete(`/torrents/${t.id}`);
                  await loadTorrents(tableBody, status);
                } catch (e) {
                  status.textContent = e instanceof Error ? e.message : String(e);
                }
              });
              wrap.append(
                pauseBtn,
                document.createTextNode(" "),
                resumeBtn,
                document.createTextNode(" "),
                delBtn,
              );
              return wrap;
            })(),
          ]),
        );
        tableBody.append(tr);
      }
    }
    status.textContent = "";
  } catch (e) {
    status.textContent = e instanceof Error ? e.message : String(e);
    tableBody.replaceChildren();
    const tr = el("tr");
    const td = el("td", { colspan: "6" });
    td.append(document.createTextNode("—"));
    tr.append(td);
    tableBody.append(tr);
  }
}

async function loadDetail(
  id: number,
  container: HTMLElement,
  status: HTMLElement,
): Promise<void> {
  status.textContent = "Загрузка…";
  container.replaceChildren();
  try {
    const data = await fetchJson<TorrentDetailOut>(`/torrents/${id}`);
    status.textContent = "";
    const pre = el("pre", { className: "json-dump" });
    pre.textContent = JSON.stringify(data, null, 2);
    const delBtn = el("button", { type: "button", className: "btn-danger" }, ["Удалить из системы"]);
    delBtn.addEventListener("click", async () => {
      if (!confirm(`Удалить торрент #${data.id}?`)) return;
      try {
        await fetchDelete(`/torrents/${data.id}`);
        setHashList();
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      } catch (e) {
        status.textContent = e instanceof Error ? e.message : String(e);
      }
    });
    container.append(
      el("p", {}, [
        el("strong", {}, [`#${data.id}`]),
        ` ${data.display_name || ""} — ${data.status}`,
        " ",
        delBtn,
      ]),
      el("p", {}, [el("code", { className: "magnet-full" }, [data.magnet_uri || "—"])]),
      el("h3", {}, ["runtime (движок)"]),
      pre,
    );
  } catch (e) {
    status.textContent = e instanceof Error ? e.message : String(e);
  }
}

function mountListShell(root: HTMLElement): void {
  const status = el("p", { className: "status" });
  const tableBody = el("tbody");

  const magnetInput = el("input", {
    type: "text",
    name: "magnet",
    placeholder: "magnet:?xt=urn:btih:…",
  }) as HTMLInputElement;

  const savePathInput = el("input", {
    type: "text",
    name: "save_path",
    placeholder: "/data",
    value: "/data",
  }) as HTMLInputElement;

  const displayNameInput = el("input", {
    type: "text",
    name: "display_name",
    placeholder: "необязательно",
  }) as HTMLInputElement;

  const form = el("form", { className: "add-form" }, [
    el("label", {}, ["Magnet URI", magnetInput]),
    el("label", {}, [
      "Каталог сохранения (на стороне движка)",
      savePathInput,
      el("span", { className: "hint" }, [
        "В Docker Compose по умолчанию том движка смонтирован в ",
        el("code", {}, ["/data"]),
        ".",
      ]),
    ]),
    el("label", {}, ["Отображаемое имя", displayNameInput]),
    el("div", { className: "actions" }, [el("button", { type: "submit" }, ["Добавить"])]),
  ]);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const magnet_uri = magnetInput.value.trim();
    const save_path = savePathInput.value.trim();
    if (!magnet_uri) return;
    if (!save_path) {
      status.textContent = "Укажите save_path.";
      return;
    }
    status.textContent = "Отправка…";
    try {
      await fetchJson<TorrentOut>("/torrents", {
        method: "POST",
        body: JSON.stringify({
          magnet_uri,
          save_path,
          display_name: displayNameInput.value.trim(),
        }),
      });
      magnetInput.value = "";
      displayNameInput.value = "";
      await loadTorrents(tableBody, status);
    } catch (e) {
      status.textContent = e instanceof Error ? e.message : String(e);
    }
  });

  const refreshBtn = el("button", { type: "button" }, ["Обновить сейчас"]);
  refreshBtn.addEventListener("click", () => loadTorrents(tableBody, status));

  const table = el("table", { className: "torrents" }, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", {}, ["id"]),
        el("th", {}, ["имя"]),
        el("th", {}, ["magnet"]),
        el("th", {}, ["save_path"]),
        el("th", {}, ["статус"]),
        el("th", {}, ["действия"]),
      ]),
    ]),
    tableBody,
  ]);

  const startPoll = () => {
    if (listPollTimer !== null) clearInterval(listPollTimer);
    listPollTimer = setInterval(() => {
      if (parseRoute().view !== "list") return;
      void loadTorrents(tableBody, status);
    }, 12000);
  };
  root.append(
    el("nav", { className: "top-nav" }, [
      el("a", { href: "#", className: "nav-link" }, ["Список"]),
    ]),
    el("h1", {}, ["Раздача торрентов"]),
    el("p", { className: "hint" }, [
      "Локально: ",
      el("code", {}, ["web"]),
      " → ",
      el("code", {}, ["npm run dev"]),
      "; Docker: UI :3000, ",
      el("code", {}, ["/api"]),
      " → ",
      el("code", {}, ["api"]),
      ". Клик по id — детали. Если на API включён ",
      el("code", {}, ["SEEDING_API_KEYS"]),
      ", в консоли: ",
      el("code", {}, ['localStorage.setItem("seedingApiKey","…")']),
      ".",
    ]),
    form,
    status,
    el("div", { className: "toolbar" }, [refreshBtn]),
    el("p", { className: "poll-hint" }, ["На списке автообновление каждые 12 с."]),
    table,
  );

  const navHome = root.querySelector("a.nav-link") as HTMLAnchorElement;
  navHome.addEventListener("click", (ev) => {
    ev.preventDefault();
    setHashList();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  void loadTorrents(tableBody, status);
  startPoll();
}

function mountDetailShell(root: HTMLElement, id: number): void {
  const status = el("p", { className: "status" });
  const main = el("div", { className: "detail-body" });

  const back = el("a", { href: "#", className: "nav-link" }, ["← К списку"]);
  back.addEventListener("click", (ev) => {
    ev.preventDefault();
    setHashList();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  root.append(
    el("nav", { className: "top-nav" }, [back]),
    el("h1", {}, [`Торрент ${id}`]),
    status,
    main,
  );

  void loadDetail(id, main, status);

  if (detailPollTimer !== null) clearInterval(detailPollTimer);
  detailPollTimer = setInterval(() => {
    const r = parseRoute();
    if (r.view !== "detail" || r.id !== id) return;
    void loadDetail(id, main, status);
  }, 8000);
}

function render(): void {
  clearViewPolls();
  const root = document.getElementById("app");
  if (!root) return;
  root.replaceChildren();
  const route = parseRoute();

  if (route.view === "list") {
    mountListShell(root);
    return;
  }

  mountDetailShell(root, route.id);
}

window.addEventListener("hashchange", () => render());
render();
