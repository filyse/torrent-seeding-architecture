/** Общий каркас приёма (добавить / обновить / загрузить): шапка, сегмент, drop-zone. */

export type ElFn = (
  tag: string,
  attrs?: Record<string, unknown>,
  children?: (string | Node)[],
) => HTMLElement;

export type IconFn = (name: string) => HTMLElement;

export function assignInputFiles(input: HTMLInputElement, files: File[]): void {
  const dt = new DataTransfer();
  for (const f of files) dt.items.add(f);
  input.files = dt.files;
}

export function intakeHead(opts: {
  el: ElFn;
  icon: IconFn;
  mark: string;
  title: string;
  titleId: string;
  lead: string;
  closeBtn: HTMLElement;
}): HTMLElement {
  const text = opts.el("div", { className: "intake-head__text" }, [
    opts.el("h2", { id: opts.titleId, className: "modal-title" }, [opts.title]),
    opts.el("p", { className: "intake-lead" }, [opts.lead]),
  ]);
  return opts.el("div", { className: "intake-head" }, [
    opts.el("div", { className: "intake-head__mark", "aria-hidden": "true" }, [opts.icon(opts.mark)]),
    text,
    opts.closeBtn,
  ]);
}

export function intakeSegment<T extends string>(opts: {
  el: ElFn;
  icon: IconFn;
  items: { id: T; icon: string; label: string }[];
  initial: T;
  onChange: (id: T) => void;
}): { el: HTMLElement; set: (id: T) => void } {
  const bar = opts.el("div", {
    className: "intake-tabs",
    role: "tablist",
  });
  const buttons: HTMLButtonElement[] = [];
  const paint = (id: T) => {
    buttons.forEach((b, i) => {
      const on = opts.items[i].id === id;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
  };
  for (const it of opts.items) {
    const b = opts.el(
      "button",
      {
        type: "button",
        className: "intake-tab",
        role: "tab",
        "data-id": it.id,
      },
      [opts.icon(it.icon), it.label],
    ) as HTMLButtonElement;
    b.addEventListener("click", () => {
      paint(it.id);
      opts.onChange(it.id);
    });
    buttons.push(b);
    bar.append(b);
  }
  paint(opts.initial);
  return { el: bar, set: paint };
}

export function intakeDrop(opts: {
  el: ElFn;
  icon: IconFn;
  accept?: string;
  multiple?: boolean;
  title: string;
  hint: string;
  browse: string;
  onFiles: (files: File[]) => void;
}): { wrap: HTMLElement; input: HTMLInputElement; show: (files: File[]) => void } {
  const inputAttrs: Record<string, unknown> = {
    type: "file",
    className: "sr-only",
    tabindex: "-1",
  };
  if (opts.accept) inputAttrs.accept = opts.accept;
  if (opts.multiple) inputAttrs.multiple = "";
  const input = opts.el("input", inputAttrs) as HTMLInputElement;

  const titleEl = opts.el("div", { className: "intake-drop__title" }, [opts.title]);
  const hintEl = opts.el("div", { className: "intake-drop__hint" }, [opts.hint]);
  const browseEl = opts.el("span", { className: "intake-drop__browse" }, [opts.browse]);
  const wrap = opts.el("div", {
    className: "intake-drop",
    role: "button",
    tabindex: "0",
    "aria-label": opts.title,
  });
  wrap.append(
    input,
    opts.el("div", { className: "intake-drop__mark", "aria-hidden": "true" }, [opts.icon("upload")]),
    titleEl,
    hintEl,
    browseEl,
  );

  const show = (files: File[]) => {
    if (files.length === 0) {
      titleEl.textContent = opts.title;
      hintEl.textContent = opts.hint;
      browseEl.textContent = opts.browse;
      wrap.setAttribute("aria-label", opts.title);
      return;
    }
    const names = files.map((f) => f.name);
    titleEl.textContent = files.length === 1 ? files[0].name : `Выбрано: ${files.length}`;
    hintEl.textContent =
      files.length === 1
        ? "Можно бросить другие файлы вместо этих"
        : names.length <= 3
          ? names.join(" · ")
          : `${names.slice(0, 2).join(" · ")} · ещё ${names.length - 2}`;
    browseEl.textContent = "Выбрать другие";
    wrap.setAttribute("aria-label", titleEl.textContent);
  };

  const apply = (list: File[]) => {
    assignInputFiles(input, list);
    show(list);
    opts.onFiles(list);
  };

  const openPicker = () => input.click();
  wrap.addEventListener("click", (ev) => {
    if (ev.target === input) return;
    openPicker();
  });
  wrap.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openPicker();
    }
  });
  input.addEventListener("change", () => {
    apply(Array.from(input.files ?? []));
  });
  wrap.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    wrap.classList.add("is-over");
  });
  wrap.addEventListener("dragleave", () => wrap.classList.remove("is-over"));
  wrap.addEventListener("drop", (ev) => {
    ev.preventDefault();
    wrap.classList.remove("is-over");
    const files = Array.from(ev.dataTransfer?.files ?? []);
    if (files.length) apply(files);
  });

  return { wrap, input, show };
}

export function intakeFoot(el: ElFn, btn: HTMLElement): HTMLElement {
  return el("div", { className: "intake-foot" }, [btn]);
}
