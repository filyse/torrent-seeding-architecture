import { animate } from "motion";
import { lockPageScroll, unlockPageScroll } from "./smoothScroll";

/** Те же пружины, что у раскрытия поиска. */
export const POP_SPRING = {
  type: "spring" as const,
  duration: 0.58,
  bounce: 0.03,
  restDelta: 0.01,
};

export type MotionCtrl = { stop: () => void; finished: Promise<unknown> };

export function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** 0 — скрыто (сдвиг 12px), 1 — на месте. rise: 1 вниз в покой, -1 вверх. */
export function applyPopProgress(el: HTMLElement, p: number, rise = 1): void {
  const clamped = Math.min(1, Math.max(0, p));
  el.style.opacity = String(clamped);
  el.style.transform = `translateZ(0) translateY(${((1 - clamped) * -12 * rise).toFixed(2)}px)`;
}

export function playPop(
  el: HTMLElement,
  open: boolean,
  prev?: MotionCtrl | null,
  rise = 1,
): MotionCtrl {
  prev?.stop();
  const to = open ? 1 : 0;
  const parsed = Number.parseFloat(el.style.opacity);
  const from = Number.isFinite(parsed) ? parsed : open ? 0 : 1;
  if (reducedMotion() || from === to) {
    applyPopProgress(el, to, rise);
    return { stop: () => undefined, finished: Promise.resolve() };
  }
  const ctrl = animate(from, to, {
    ...POP_SPRING,
    onUpdate: (v) => applyPopProgress(el, Number(v), rise),
  }) as MotionCtrl;
  if (open) {
    void ctrl.finished.then(() => {
      el.style.opacity = "";
      el.style.transform = "";
    });
  }
  return ctrl;
}

function bumpPop(el: HTMLElement): number {
  const n = (Number(el.dataset.popn) || 0) + 1;
  el.dataset.popn = String(n);
  return n;
}

/** Открыть абсолютную панель (меню): снимаем hidden и пружиним из -12px. */
export function openPopPanel(el: HTMLElement, prev?: MotionCtrl | null, rise = 1): MotionCtrl {
  bumpPop(el);
  if (el.hidden) applyPopProgress(el, 0, rise);
  el.hidden = false;
  el.style.pointerEvents = "auto";
  return playPop(el, true, prev, rise);
}

/** Закрыть сразу. Fade при закрытии оставляет «призраков» под кнопкой. */
export function closePopPanel(el: HTMLElement, prev?: MotionCtrl | null, _rise = 1): MotionCtrl {
  prev?.stop();
  bumpPop(el);
  el.hidden = true;
  el.style.pointerEvents = "none";
  el.style.opacity = "";
  el.style.transform = "";
  return { stop: () => undefined, finished: Promise.resolve() };
}

/** Каскад появления строк (поиск, список). Дальше 8-й — без добавки к задержке. */
export function staggerIn(els: Iterable<Element>, delay = 0.03, rise = 1): MotionCtrl[] {
  const list = [...els].filter((n): n is HTMLElement => n instanceof HTMLElement);
  if (reducedMotion()) {
    for (const el of list) applyPopProgress(el, 1, rise);
    return [];
  }
  return list.map((el, i) => {
    applyPopProgress(el, 0, rise);
    const ctrl = animate(0, 1, {
      ...POP_SPRING,
      delay: Math.min(i, 8) * delay,
      onUpdate: (v) => applyPopProgress(el, Number(v), rise),
    }) as MotionCtrl;
    void ctrl.finished.then(() => {
      el.style.opacity = "";
      el.style.transform = "";
    });
    return ctrl;
  });
}

/** Панель у якоря: opacity + scale, без translateY (не проваливается). */
export function applyAnchorProgress(el: HTMLElement, p: number): void {
  const clamped = Math.min(1, Math.max(0, p));
  el.style.opacity = String(clamped);
  const s = 0.96 + 0.04 * clamped;
  el.style.transform = `translateZ(0) scale(${s.toFixed(4)})`;
}

/** Открыть список у поля: растёт от края, не едет вниз. */
export function openAnchorPanel(
  el: HTMLElement,
  origin: "top" | "bottom",
  prev?: MotionCtrl | null,
): MotionCtrl {
  prev?.stop();
  bumpPop(el);
  el.hidden = false;
  el.style.pointerEvents = "auto";
  el.style.transformOrigin = origin === "top" ? "50% 0%" : "50% 100%";
  if (reducedMotion()) {
    applyAnchorProgress(el, 1);
    return { stop: () => undefined, finished: Promise.resolve() };
  }
  applyAnchorProgress(el, 0);
  return animate(0, 1, {
    ...POP_SPRING,
    onUpdate: (v) => applyAnchorProgress(el, Number(v)),
  }) as MotionCtrl;
}

/** Пружина Smooth Dropdown (21st @0xUrvish/smooth-dropdown). */
export const MORPH_SPRING = {
  type: "spring" as const,
  damping: 34,
  stiffness: 380,
  mass: 0.8,
};

export const INDICATOR_SPRING = {
  type: "spring" as const,
  damping: 30,
  stiffness: 520,
  mass: 0.8,
};

export const EASE_OUT_QUINT = [0.23, 1, 0.32, 1] as const;

export function morphBox(
  el: HTMLElement,
  to: { w: number; h: number; radius: number },
  prev?: MotionCtrl | null,
): MotionCtrl {
  prev?.stop();
  if (reducedMotion()) {
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.style.borderRadius = `${to.radius}px`;
    return { stop: () => undefined, finished: Promise.resolve() };
  }
  return animate(
    el,
    { width: to.w, height: to.h, borderRadius: to.radius },
    MORPH_SPRING,
  ) as MotionCtrl;
}

const MODAL_SCRIM = 0.42;

export function applyModalProgress(overlay: HTMLElement, dialog: HTMLElement, p: number): void {
  const clamped = Math.min(1, Math.max(0, p));
  overlay.style.backgroundColor = `rgba(0, 0, 0, ${(clamped * MODAL_SCRIM).toFixed(3)})`;
  applyPopProgress(dialog, clamped);
}

/** Затемнение + окно на той же пружине, что поиск. close() гасит и снимает с DOM. */
export function bindModal(overlay: HTMLElement): { close: () => Promise<void> } {
  const dialog = overlay.querySelector<HTMLElement>(".modal-dialog, .modal-panel");
  if (!dialog) {
    return {
      close: async () => {
        overlay.remove();
      },
    };
  }
  let closing = false;
  let motion: MotionCtrl | null = null;
  applyModalProgress(overlay, dialog, 0);
  overlay.style.pointerEvents = "none";
  lockPageScroll();

  const run = (open: boolean): Promise<void> => {
    motion?.stop();
    const to = open ? 1 : 0;
    const parsed = Number.parseFloat(dialog.style.opacity);
    const from = Number.isFinite(parsed) ? parsed : open ? 0 : 1;
    overlay.style.pointerEvents = open ? "auto" : "none";
    if (reducedMotion() || from === to) {
      applyModalProgress(overlay, dialog, to);
      return Promise.resolve();
    }
    motion = animate(from, to, {
      ...POP_SPRING,
      onUpdate: (v) => applyModalProgress(overlay, dialog, Number(v)),
    }) as MotionCtrl;
    return motion.finished.then(() => undefined);
  };

  requestAnimationFrame(() => {
    if (!closing && overlay.isConnected) void run(true);
  });

  return {
    close: async () => {
      if (closing) return;
      closing = true;
      await run(false);
      unlockPageScroll();
      overlay.remove();
    },
  };
}

export function presentModal(overlay: HTMLElement): () => Promise<void> {
  const { close } = bindModal(overlay);
  document.body.append(overlay);
  return close;
}

/** Слот в потоке: высота 0↔auto + прозрачность. Для тулбара выделения. */
export function playFold(slot: HTMLElement, open: boolean, prev?: MotionCtrl | null): MotionCtrl {
  prev?.stop();
  const inner = slot.firstElementChild instanceof HTMLElement ? slot.firstElementChild : null;
  // Мерить старт надо до снятия hidden. У скрытого слота высоты нет, но на первом
  // открытии инлайновой высоты ещё нет тоже — сняв hidden, мы получили бы уже
  // натуральную высоту, from совпал бы с to, и слот раскрылся бы рывком вместе со
  // всем списком под ним. Со второго раза высота остаётся от закрытия («0px»).
  const from = slot.hidden ? 0 : slot.getBoundingClientRect().height;
  slot.hidden = false;
  slot.style.overflow = "hidden";
  slot.style.height = "auto";
  const mb = inner ? Number.parseFloat(getComputedStyle(inner).marginBottom) || 0 : 0;
  const natural = inner ? inner.getBoundingClientRect().height + mb : 0;
  const to = open ? natural : 0;
  slot.style.height = `${from}px`;
  const my = bumpPop(slot);
  if (reducedMotion() || from === to) {
    slot.style.height = open ? "auto" : "0px";
    slot.style.opacity = open ? "1" : "0";
    if (!open) slot.hidden = true;
    return { stop: () => undefined, finished: Promise.resolve() };
  }
  const ctrl = animate(0, 1, {
    ...POP_SPRING,
    onUpdate: (v) => {
      const t = Number(v);
      slot.style.height = `${from + (to - from) * t}px`;
      slot.style.opacity = String(open ? t : 1 - t);
    },
  }) as MotionCtrl;
  void ctrl.finished.then(() => {
    if (Number(slot.dataset.popn) !== my) return;
    if (open) {
      slot.style.height = "auto";
      slot.style.overflow = "";
      slot.style.opacity = "";
    } else {
      slot.style.height = "0px";
      slot.hidden = true;
    }
  });
  return ctrl;
}
