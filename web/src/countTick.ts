/** Плавный счётчик для живых цифр (док «Обновлено»). */

import { animate } from "motion";
import { reducedMotion, type MotionCtrl } from "./motionPop";

export type TickFmt = (n: number) => string;

export const CHIP_TICK_SEL = ".tray-toggle__val";

const TICK_SPRING = {
  type: "spring" as const,
  duration: 0.68,
  bounce: 0.04,
  restDelta: 0.4,
};

const SHUFFLE_MS = 52;

type TickState = {
  ctrl: MotionCtrl | null;
  shown: number;
  to: number;
  fmt: TickFmt;
  hold: boolean;
  gen: number;
  shuffle: number;
};

const ticks = new WeakMap<HTMLElement, TickState>();

function revealing(el: HTMLElement): boolean {
  return el.closest("[data-tick-reveal]") != null;
}

function eachTick(root: ParentNode, sel: string): HTMLElement[] {
  return [...root.querySelectorAll(sel)].filter((n): n is HTMLElement => n instanceof HTMLElement);
}

function bumpGen(st: TickState): number {
  st.gen += 1;
  return st.gen;
}

function stopShuffle(st: TickState): void {
  if (st.shuffle) {
    window.clearInterval(st.shuffle);
    st.shuffle = 0;
  }
}

/** Случайное число того же порядка, чтобы ширина и единицы не прыгали. */
function scrambleFrom(to: number, fmt: TickFmt): number {
  if (!Number.isFinite(to) || to === 0) return 0;
  const sign = to < 0 ? -1 : 1;
  const abs = Math.abs(to);
  const exp = Math.floor(Math.log10(abs));
  const base = 10 ** exp;
  const want = fmt(to).length;
  for (let i = 0; i < 10; i += 1) {
    let m = 1 + Math.random() * 8.6;
    const cur = abs / base;
    if (Math.abs(m - cur) < 0.45) m = cur > 5 ? 2.1 + Math.random() * 1.4 : 7.1 + Math.random() * 1.6;
    const n = sign * m * base;
    if (fmt(n).length <= want + 1) return n;
  }
  return sign * abs * (0.46 + Math.random() * 0.38);
}

function paintScramble(el: HTMLElement, st: TickState): void {
  const n = scrambleFrom(st.to, st.fmt);
  st.shown = n;
  el.textContent = st.fmt(n);
}

function startShuffle(el: HTMLElement, st: TickState): void {
  stopShuffle(st);
  paintScramble(el, st);
  st.shuffle = window.setInterval(() => {
    if (!st.hold) {
      stopShuffle(st);
      return;
    }
    paintScramble(el, st);
  }, SHUFFLE_MS);
}

export function tickNumber(el: HTMLElement, to: number | null, fmt: TickFmt): void {
  if (to == null || !Number.isFinite(to)) {
    const prev = ticks.get(el);
    if (prev) {
      prev.ctrl?.stop();
      stopShuffle(prev);
      bumpGen(prev);
    }
    ticks.delete(el);
    el.textContent = "—";
    return;
  }
  const prev = ticks.get(el);
  if (!prev) {
    if (revealing(el) && !reducedMotion()) {
      const st: TickState = { ctrl: null, shown: to, to, fmt, hold: true, gen: 0, shuffle: 0 };
      ticks.set(el, st);
      startShuffle(el, st);
      return;
    }
    el.textContent = fmt(to);
    ticks.set(el, { ctrl: null, shown: to, to, fmt, hold: false, gen: 0, shuffle: 0 });
    return;
  }
  prev.fmt = fmt;
  if (prev.hold) {
    prev.to = to;
    return;
  }
  if (Math.abs(prev.to - to) < 1e-6 && !prev.ctrl && Math.abs(prev.shown - to) < 1e-6) {
    el.textContent = fmt(to);
    return;
  }
  prev.ctrl?.stop();
  stopShuffle(prev);
  if (reducedMotion()) {
    el.textContent = fmt(to);
    ticks.set(el, { ctrl: null, shown: to, to, fmt, hold: false, gen: prev.gen, shuffle: 0 });
    return;
  }
  const start = prev.shown;
  const ctrl = animate(start, to, {
    ...TICK_SPRING,
    onUpdate: (v) => {
      const n = Number(v);
      prev.shown = n;
      el.textContent = fmt(n);
    },
  }) as MotionCtrl;
  const my = bumpGen(prev);
  void ctrl.finished.then(() => {
    if (ticks.get(el) === prev && prev.ctrl === ctrl && prev.gen === my) {
      prev.ctrl = null;
      prev.shown = to;
      el.textContent = fmt(to);
    }
  });
  prev.ctrl = ctrl;
  prev.to = to;
}

/** Жёсткая ширина чипа: скрембл не раздувает и не сжимает плашку. */
export function lockTickMinWidth(root: ParentNode, sel: string): void {
  for (const el of eachTick(root, sel)) {
    const w = el.getBoundingClientRect().width;
    if (w <= 0) continue;
    const px = `${Math.ceil(w)}px`;
    el.style.width = px;
    el.style.minWidth = px;
    el.style.maxWidth = px;
  }
}

export function unlockTickMinWidth(root: ParentNode, sel: string): void {
  for (const el of eachTick(root, sel)) {
    el.style.width = "";
    el.style.minWidth = "";
    el.style.maxWidth = "";
  }
}

/** Пауза на случайных цифрах: живые апдейты только меняют цель. */
export function holdTicks(root: ParentNode, sel: string): void {
  if (reducedMotion()) return;
  for (const el of eachTick(root, sel)) {
    const st = ticks.get(el);
    if (!st) continue;
    st.ctrl?.stop();
    st.ctrl = null;
    bumpGen(st);
    st.hold = true;
    startShuffle(el, st);
  }
}

/** После выезда — от последней случайной до актуальной цели. */
export function releaseTicks(root: ParentNode, sel: string, stagger = 0): void {
  const list = eachTick(root, sel);
  list.forEach((el, i) => {
    const st = ticks.get(el);
    if (!st?.hold) return;
    stopShuffle(st);
    const my = bumpGen(st);
    const run = (): void => {
      const cur = ticks.get(el);
      if (!cur?.hold || cur.gen !== my) return;
      cur.hold = false;
      tickNumber(el, cur.to, cur.fmt);
    };
    const delay = stagger > 0 ? Math.min(i, 8) * stagger * 1000 : 0;
    if (delay > 0) window.setTimeout(run, delay);
    else run();
  });
}

/** Закрыли меню — сразу реальные цифры. */
export function dropHold(root: ParentNode, sel: string): void {
  for (const el of eachTick(root, sel)) {
    const st = ticks.get(el);
    if (!st) continue;
    st.ctrl?.stop();
    stopShuffle(st);
    st.ctrl = null;
    bumpGen(st);
    st.hold = false;
    st.shown = st.to;
    el.textContent = st.fmt(st.to);
  }
  unlockTickMinWidth(root, sel);
}
