/** Список каталогов в «Создать» / «Загрузить»: крошки и строки в языке кабинета. */

import type { ElFn, IconFn } from "./intake";

export function fillCreatorCrumbs(opts: {
  host: HTMLElement;
  el: ElFn;
  icon: IconFn;
  path: string;
  onGo: (path: string) => void;
}): void {
  opts.host.replaceChildren();
  const parts = opts.path.split("/").filter(Boolean);
  const addSep = () => {
    opts.host.append(
      opts.el("span", { className: "creator-crumb-sep", "aria-hidden": "true" }, [
        opts.icon("chevron-right"),
      ]),
    );
  };
  const root = opts.el("button", { type: "button", className: "creator-crumb" }, ["Диск"]) as HTMLButtonElement;
  if (parts.length === 0) root.classList.add("is-now");
  root.addEventListener("click", () => opts.onGo(""));
  opts.host.append(root);
  let acc = "";
  parts.forEach((part, i) => {
    acc = acc ? `${acc}/${part}` : part;
    const target = acc;
    const last = i === parts.length - 1;
    addSep();
    const crumb = opts.el(
      "button",
      { type: "button", className: last ? "creator-crumb is-now" : "creator-crumb" },
      [part],
    ) as HTMLButtonElement;
    crumb.addEventListener("click", () => opts.onGo(target));
    opts.host.append(crumb);
  });
}

export function creatorBrowseRow(opts: {
  el: ElFn;
  icon: IconFn;
  name: string;
  isDir: boolean;
  sizeText?: string;
  checked?: boolean;
  onCheck?: (on: boolean) => void;
  onOpen?: () => void;
}): HTMLElement {
  const row = opts.el("div", {
    className: `creator-row${opts.isDir ? " is-dir" : ""}${opts.checked ? " is-on" : ""}`,
  });
  if (opts.onCheck) {
    const tick = opts.el("input", {
      type: "checkbox",
      className: "creator-tick",
    }) as HTMLInputElement;
    tick.checked = Boolean(opts.checked);
    tick.addEventListener("click", (ev) => ev.stopPropagation());
    tick.addEventListener("change", () => {
      row.classList.toggle("is-on", tick.checked);
      opts.onCheck?.(tick.checked);
    });
    row.append(tick);
  }
  row.append(
    opts.el("span", { className: "creator-row__mark", "aria-hidden": "true" }, [
      opts.icon(opts.isDir ? "folder" : "file"),
    ]),
  );
  const labelKids: (string | Node)[] = [opts.name];
  const nameEl = opts.onOpen
    ? (opts.el("button", { type: "button", className: "creator-name creator-name--dir" }, labelKids) as HTMLButtonElement)
    : opts.el("span", { className: "creator-name" }, labelKids);
  if (opts.onOpen) {
    (nameEl as HTMLButtonElement).addEventListener("click", opts.onOpen);
  }
  row.append(nameEl);
  if (opts.sizeText) {
    row.append(opts.el("span", { className: "creator-row__meta" }, [opts.sizeText]));
  }
  return row;
}
