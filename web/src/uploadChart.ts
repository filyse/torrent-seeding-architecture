/** SVG-графики отдачи: ферма на /network/history, мини — в карточках /network/uploaded. */

export type HistoryPeriod = "day" | "week" | "month";

export const HISTORY_PERIODS: { id: HistoryPeriod; label: string }[] = [
  { id: "day", label: "День" },
  { id: "week", label: "Неделя" },
  { id: "month", label: "Месяц" },
];

export type HistoryBucket = {
  t: string;
  farm: number;
  wan: Record<string, number>;
  engines?: Record<string, number>;
  sampled?: boolean;
};

export type UploadedHistory = {
  period: HistoryPeriod;
  buckets: HistoryBucket[];
  total: { farm: number; wan: Record<string, number> };
  previous_total: { farm: number; wan: Record<string, number> };
  first_sampled_at?: string | null;
  last_sampled_at?: string | null;
};

import { renderCellChart } from "./cellChart";

const WAN_ORDER = ["wan1", "wan2"];

export function wanColor(id: string): string {
  if (id === "wan1") return "var(--accent)";
  if (id === "wan2") return "var(--success)";
  return "var(--muted)";
}

export function wanName(id: string): string {
  if (id === "wan1") return "WAN1";
  if (id === "wan2") return "WAN2";
  return id;
}

export function periodPhrase(period: HistoryPeriod): string {
  if (period === "day") return "за сутки";
  if (period === "week") return "за неделю";
  return "за 30 дней";
}

export function previousPhrase(period: HistoryPeriod): string {
  if (period === "day") return "против предыдущих суток";
  if (period === "week") return "против прошлой недели";
  return "против предыдущих 30 дней";
}

export function avgUnit(period: HistoryPeriod): string {
  return period === "day" ? "час" : "день";
}

export function bucketSampled(b: HistoryBucket): boolean {
  return b.sampled === true;
}

export function fmtChartBytes(v: number): string {
  if (!Number.isFinite(v) || v < 0) return "—";
  if (v === 0) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"] as const;
  let n = v;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  if (i === 0) return `${n} ${units[i]}`;
  return `${n.toFixed(n >= 10 || i === 1 ? 0 : 1)} ${units[i]}`;
}

export function bucketRangeLabel(iso: string, period: HistoryPeriod): string {
  const start = new Date(iso);
  if (Number.isNaN(start.getTime())) return iso;
  if (period === "day") {
    const end = new Date(start.getTime() + 3_600_000);
    const day = start.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
    const a = start.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hour12: false });
    const b = end.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hour12: false });
    return `${day}, ${a}–${b}`;
  }
  return start.toLocaleDateString("ru-RU", {
    weekday: period === "week" ? "short" : undefined,
    day: "numeric",
    month: "short",
  });
}


function uniqueWans(history: UploadedHistory): string[] {
  const seen = new Set<string>(WAN_ORDER);
  for (const b of history.buckets) {
    for (const id of Object.keys(b.wan)) seen.add(id);
  }
  return [...seen].filter((id) => WAN_ORDER.includes(id) || history.buckets.some((b) => (b.wan[id] ?? 0) > 0));
}

function bucketLabel(iso: string, period: HistoryPeriod, index: number, n: number, prevIso?: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  if (period === "day") {
    if (index % 4 !== 0 && index !== n - 1) return "";
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", hour12: false });
  }
  if (period === "week") {
    // Телефон: подпись дня — только на первой корзине дня (иначе «срсрср» сливается).
    if (narrowScreen() && prevIso) {
      const p = new Date(prevIso);
      if (!Number.isNaN(p.getTime()) && p.getDate() === d.getDate()) return "";
    }
    return d.toLocaleDateString("ru-RU", { weekday: "short" });
  }
  if (index % 5 !== 0 && index !== n - 1) return "";
  return d.toLocaleDateString("ru-RU", { day: "numeric" });
}

function narrowScreen(): boolean {
  return typeof window !== "undefined" && window.innerWidth <= 640;
}

/** Большой график «Истории» — общий рендер «квадратиков» (cellChart.ts). */
export function renderFarmChart(history: UploadedHistory, widthPx = 680, dpr = 1): SVGSVGElement {
  const wanOrder = uniqueWans(history);
  const n = history.buckets.length;
  const sampledVals = history.buckets.filter(bucketSampled).map((b) =>
    wanOrder.reduce((s, id) => s + (b.wan[id] ?? 0), 0),
  );
  return renderCellChart({
    width: widthPx,
    height: 260,
    dpr,
    pad: { l: 46, r: 8, t: 10, b: 24 },
    cols: history.buckets.map((b, i) => ({
      sampled: bucketSampled(b),
      segs: wanOrder.map((id) => ({ id: id === "wan2" ? "wan2" : "wan1", v: b.wan[id] ?? 0 })),
      label: bucketLabel(b.t, history.period, i, n, history.buckets[i - 1]?.t),
    })),
    max: Math.max(1, ...sampledVals),
    axisFmt: fmtChartBytes,
    maxCell: 14,
    // Телефон: клетка считается от ширины экрана (до ~4px) — график целиком, без прокрутки.
    minCell: narrowScreen() ? 4 : 10,
    cls: "upload-chart--farm",
    uid: "farm",
    aria: "Отдача фермы за период",
  });
}

/** Мини-график канала (вкладки «Сети»): те же «квадратики», клетки мельче. */
export function renderMiniChart(
  history: UploadedHistory,
  wanId: string,
  widthPx = 320,
  dpr = 1,
): SVGSVGElement {
  const n = history.buckets.length;
  const values = history.buckets.map((b) => (bucketSampled(b) ? (b.wan[wanId] ?? 0) : 0));
  const peak = Math.max(0, ...values);
  const pal = wanId === "wan2" ? "wan2" : "wan1";
  return renderCellChart({
    width: widthPx,
    height: 120,
    dpr,
    pad: { l: 46, r: 6, t: 8, b: 20 },
    cols: history.buckets.map((b, i) => ({
      sampled: bucketSampled(b),
      segs: [{ id: pal, v: values[i] }],
      label: bucketLabel(b.t, history.period, i, n, history.buckets[i - 1]?.t),
      peak: bucketSampled(b) && peak > 0 && values[i] === peak,
    })),
    max: Math.max(1, ...values),
    axisFmt: fmtChartBytes,
    maxCell: 8,
    minCell: narrowScreen() ? 3 : 5,
    gap: 1.5,
    cls: "upload-chart--mini",
    uid: `mini-${wanId}`,
    aria: `Отдача ${wanName(wanId)} за период`,
  });
}

export { cellFills as farmCellFills, morphCells as morphFarmCells } from "./cellChart";

export function bindFarmHover(
  svg: SVGSVGElement,
  history: UploadedHistory,
  tip: HTMLElement,
  fmtBytes: (n: number) => string,
): () => void {
  const hide = () => {
    tip.hidden = true;
    svg.querySelectorAll(".upload-chart__col.is-hover").forEach((n) => n.classList.remove("is-hover"));
  };

  const move = (ev: PointerEvent) => {
    const hit = (ev.target as Element | null)?.closest?.("[data-bucket]");
    if (!hit) {
      hide();
      return;
    }
    const i = Number(hit.getAttribute("data-bucket"));
    const b = history.buckets[i];
    if (!b) {
      hide();
      return;
    }
    svg.querySelectorAll(".upload-chart__col.is-hover").forEach((n) => n.classList.remove("is-hover"));
    hit.closest(".upload-chart__col")?.classList.add("is-hover");

    const title = bucketRangeLabel(b.t, history.period);
    if (!bucketSampled(b)) {
      tip.replaceChildren();
      const h = document.createElement("div");
      h.className = "upload-tip__title";
      h.textContent = title;
      const n = document.createElement("div");
      n.className = "upload-tip__muted";
      n.textContent = "данных ещё нет";
      tip.append(h, n);
    } else {
      tip.replaceChildren();
      const h = document.createElement("div");
      h.className = "upload-tip__title";
      h.textContent = title;
      tip.append(h);
      for (const id of uniqueWans(history)) {
        const row = document.createElement("div");
        row.className = "upload-tip__row";
        const sw = document.createElement("span");
        sw.className = `upload-tip__swatch upload-tip__swatch--${id}`;
        const lab = document.createElement("span");
        lab.textContent = wanName(id);
        const val = document.createElement("span");
        val.textContent = fmtBytes(b.wan[id] ?? 0);
        row.append(sw, lab, val);
        tip.append(row);
      }
      const sum = document.createElement("div");
      sum.className = "upload-tip__sum";
      sum.textContent = `всего ${fmtBytes(b.farm)}`;
      tip.append(sum);
    }

    tip.hidden = false;
    placeTip(tip, ev);
  };

  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", hide);
  return () => {
    svg.removeEventListener("pointermove", move);
    svg.removeEventListener("pointerleave", hide);
    hide();
  };
}

function placeTip(tip: HTMLElement, ev: PointerEvent): void {
  // Фиксируем к окну: иначе ячейка мини-графика (~120px) режет карточку,
  // а при hidden ширина ещё 0 — первый кадр «неполный», пока не сдвинешь мышь.
  void tip.offsetWidth;
  const pad = 8;
  const gap = 12;
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  let x = ev.clientX + gap;
  let y = ev.clientY + gap;
  if (x + tw > window.innerWidth - pad) x = ev.clientX - tw - gap;
  if (y + th > window.innerHeight - pad) y = ev.clientY - th - gap;
  // Телефон: подсказка не выходит за карточку графика.
  if (window.innerWidth <= 640) {
    const card = (ev.target as Element | null)?.closest?.(".panel, .wan-card, .upload-history");
    if (card) {
      const r = card.getBoundingClientRect();
      x = Math.min(Math.max(x, r.left + pad), r.right - tw - pad);
    }
  }
  tip.style.left = `${Math.max(pad, x)}px`;
  tip.style.top = `${Math.max(pad, y)}px`;
}

function tipNode(className: string, text: string): HTMLElement {
  const n = document.createElement("div");
  n.className = className;
  n.textContent = text;
  return n;
}

export function bindMiniHover(
  svg: SVGSVGElement,
  history: UploadedHistory,
  wanId: string,
  engines: string[],
  tip: HTMLElement,
  fmtBytes: (n: number) => string,
): () => void {
  const sampled = history.buckets.filter(bucketSampled);
  const avg = sampled.length
    ? sampled.reduce((s, b) => s + (b.wan[wanId] ?? 0), 0) / sampled.length
    : 0;
  const periodTotal = history.total.wan[wanId] ?? 0;
  const peak = sampled.reduce((m, b) => Math.max(m, b.wan[wanId] ?? 0), 0);

  const hide = () => {
    tip.hidden = true;
    svg.querySelectorAll(".upload-chart__col.is-hover").forEach((n) => n.classList.remove("is-hover"));
  };

  const move = (ev: PointerEvent) => {
    const hit = (ev.target as Element | null)?.closest?.("[data-bucket]");
    if (!hit) {
      hide();
      return;
    }
    const i = Number(hit.getAttribute("data-bucket"));
    const b = history.buckets[i];
    if (!b) {
      hide();
      return;
    }
    svg.querySelectorAll(".upload-chart__col.is-hover").forEach((n) => n.classList.remove("is-hover"));
    hit.closest(".upload-chart__col")?.classList.add("is-hover");

    const title = bucketRangeLabel(b.t, history.period);
    tip.replaceChildren();
    tip.append(tipNode("upload-tip__title", title));
    if (!bucketSampled(b)) {
      tip.append(tipNode("upload-tip__muted", "данных ещё нет"));
    } else {
      const wan = b.wan[wanId] ?? 0;
      tip.append(tipNode("upload-tip__value", fmtBytes(wan)));
      const bits: string[] = [];
      if (b.farm > 0) bits.push(`${((wan / b.farm) * 100).toFixed(0)}% фермы`);
      if (periodTotal > 0) bits.push(`${((wan / periodTotal) * 100).toFixed(0)}% ${periodPhrase(history.period)}`);
      if (avg > 0 && wan > 0) bits.push(`×${(wan / avg).toFixed(1)} среднего`);
      if (peak > 0 && wan === peak) bits.push("пик");
      if (bits.length) tip.append(tipNode("upload-tip__sum", bits.join(" · ")));

      const rows = engines
        .map((id) => ({ id, v: b.engines?.[id] ?? 0 }))
        .filter((r) => r.v > 0)
        .sort((a, c) => c.v - a.v);
      const shown = rows.slice(0, 6);
      for (const r of shown) {
        const row = document.createElement("div");
        row.className = "upload-tip__row";
        const sw = document.createElement("span");
        sw.className = `upload-tip__swatch upload-tip__swatch--${wanId}`;
        const lab = document.createElement("span");
        lab.textContent = r.id;
        const val = document.createElement("span");
        val.textContent = wan > 0 ? `${fmtBytes(r.v)} · ${((r.v / wan) * 100).toFixed(0)}%` : fmtBytes(r.v);
        row.append(sw, lab, val);
        tip.append(row);
      }
      if (rows.length > shown.length) {
        tip.append(tipNode("upload-tip__muted", `ещё ${rows.length - shown.length} движков`));
      }
      if (wan <= 0) tip.append(tipNode("upload-tip__muted", "в этом интервале тишина"));
    }
    tip.hidden = false;
    placeTip(tip, ev);
  };

  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", hide);
  return () => {
    svg.removeEventListener("pointermove", move);
    svg.removeEventListener("pointerleave", hide);
    hide();
  };
}

/* ── Плавная смена данных фермерского графика ──────────────────────────────
   Новый SVG строится как раньше, затем у столбиков на тех же позициях
   (ключ — номер столбика + WAN) плавно меняется только высота. Ни сдвигов,
   ни роста от нуля; смену периода main.ts показывает мягким проявлением.
   Один твин на весь график (motion animate(0→1) + onUpdate).
   prefers-reduced-motion — сразу итог. */
type Geo = { x: number; y: number; w: number; h: number };

function geoOf(r: Element): Geo {
  return {
    x: Number(r.getAttribute("x")),
    y: Number(r.getAttribute("y")),
    w: Number(r.getAttribute("width")),
    h: Number(r.getAttribute("height")),
  };
}

export function farmChartGeometry(svg: SVGSVGElement | null): Map<string, Geo> {
  const out = new Map<string, Geo>();
  if (!svg) return out;
  for (const r of svg.querySelectorAll("rect[data-key]")) out.set(r.getAttribute("data-key")!, geoOf(r));
  return out;
}

export type ChartTween = (from: number, to: number, onUpdate: (p: number) => void) => { stop: () => void };

export function morphFarmChart(
  prev: Map<string, Geo>,
  next: SVGSVGElement,
  tween: ChartTween,
): (() => void) | null {
  if (prev.size === 0) return null;
  if (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) return null;
  const jobs: { r: Element; a: Geo; b: Geo }[] = [];
  for (const r of next.querySelectorAll("rect[data-key]")) {
    const b = geoOf(r);
    const old = prev.get(r.getAttribute("data-key")!);
    // Новый столбик растёт от нижней оси на своём месте.
    // Только высота на своём месте (ключ — позиция столбика): без сдвигов и роста от нуля.
    if (!old) continue;
    const a = { x: b.x, y: old.y, w: b.w, h: old.h };
    if (a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h) continue;
    jobs.push({ r, a, b });
  }
  if (!jobs.length) return null;
  const set = (p: number) => {
    for (const { r, a, b } of jobs) {
      r.setAttribute("x", (a.x + (b.x - a.x) * p).toFixed(2));
      r.setAttribute("y", (a.y + (b.y - a.y) * p).toFixed(2));
      r.setAttribute("width", Math.max(0, a.w + (b.w - a.w) * p).toFixed(2));
      r.setAttribute("height", Math.max(0, a.h + (b.h - a.h) * p).toFixed(2));
    }
  };
  set(0);
  const anim = tween(0, 1, set);
  return () => {
    anim.stop();
    set(1);
  };
}
