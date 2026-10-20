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

const NS = "http://www.w3.org/2000/svg";
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

function svgEl(name: string, attrs: Record<string, string | number> = {}): SVGElement {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
}

function uniqueWans(history: UploadedHistory): string[] {
  const seen = new Set<string>(WAN_ORDER);
  for (const b of history.buckets) {
    for (const id of Object.keys(b.wan)) seen.add(id);
  }
  return [...seen].filter((id) => WAN_ORDER.includes(id) || history.buckets.some((b) => (b.wan[id] ?? 0) > 0));
}

function bucketLabel(iso: string, period: HistoryPeriod, index: number, n: number): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  if (period === "day") {
    if (index % 4 !== 0 && index !== n - 1) return "";
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", hour12: false });
  }
  if (period === "week") {
    return d.toLocaleDateString("ru-RU", { weekday: "short" });
  }
  if (index % 5 !== 0 && index !== n - 1) return "";
  return d.toLocaleDateString("ru-RU", { day: "numeric" });
}

/** Большой график «Истории»: колонки «квадратиков». Раскладка — в CSS-пикселях
 *  реальной ширины (viewBox == размер элемента, без растяжения), каждая клетка
 *  и зазор кратны физическому пикселю (round(n·dpr)/dpr) — края чёткие при
 *  DPR 1 / 1.25 / 1.5 / 2. Клетки — отдельные rect (без <pattern>: он ресэмплится). */
export function renderFarmChart(history: UploadedHistory, widthPx = 680, dpr = 1): SVGSVGElement {
  const d = Math.max(1, dpr);
  const snap = (v: number) => Math.round(v * d) / d;
  const W = Math.max(320, Math.round(widthPx));
  const H = 260;
  const pad = { l: 46, r: 8, t: 10, b: 24 };
  const innerW = W - pad.l - pad.r;
  const n = Math.max(1, history.buckets.length);
  const slot = innerW / n;
  const gap = snap(2);
  const cs = Math.max(snap(4), snap(Math.min(14, slot - Math.max(2, slot * 0.2))));
  const u = cs + gap;
  const baseY = H - pad.b;
  const rows = Math.max(1, Math.floor((baseY - pad.t) / u));
  const innerH = rows * u;
  const top = baseY - innerH;
  const rx = cs >= 8 ? 2 : 1;
  const wanOrder = uniqueWans(history);
  const sampledVals = history.buckets.filter(bucketSampled).map((b) =>
    wanOrder.reduce((s, id) => s + (b.wan[id] ?? 0), 0),
  );
  const max = Math.max(1, ...sampledVals);

  const root = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    width: W,
    height: H,
    class: "upload-chart upload-chart--farm",
    role: "img",
    "aria-label": "Отдача фермы за период",
  }) as SVGSVGElement;
  root.style.width = `${W}px`;
  root.style.height = `${H}px`;

  const defs = svgEl("defs");
  const pat = svgEl("pattern", {
    id: "upload-unsampled",
    width: 5,
    height: 5,
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(35)",
  });
  pat.appendChild(
    svgEl("path", { d: "M0 0 H5", stroke: "currentColor", "stroke-width": 1, class: "upload-chart__hatch" }),
  );
  defs.appendChild(pat);
  const reveal = svgEl("clipPath", { id: "upload-cells-reveal" });
  reveal.appendChild(
    svgEl("rect", {
      x: 0,
      y: 0,
      width: W,
      height: H,
      class: "upload-chart__reveal",
      "data-base": String(baseY),
      "data-dpr": String(d),
    }),
  );
  defs.appendChild(reveal);
  root.appendChild(defs);
  // Сдвиг на дробную долю физического пикселя (позиция svg на странице) — main.ts.
  const stage = svgEl("g", { class: "upload-chart__stage" });
  root.appendChild(stage);

  for (const frac of [0, 0.5, 1]) {
    const y = snap(top + innerH * (1 - frac)) + 0.5 / d;
    stage.appendChild(
      svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: "upload-chart__grid" }),
    );
    const lab = svgEl("text", {
      x: pad.l - 6,
      y: (y + 3).toFixed(2),
      "text-anchor": "end",
      class: "upload-chart__axis",
    });
    lab.textContent = frac === 0 ? "0" : fmtChartBytes(max * frac);
    stage.appendChild(lab);
  }

  const colsWrap = svgEl("g", { class: "upload-chart__cols", "clip-path": "url(#upload-cells-reveal)" });
  stage.appendChild(colsWrap);

  history.buckets.forEach((b, i) => {
    const x = pad.l + i * slot;
    const cx = snap(x + (slot - cs) / 2);
    const g = svgEl("g", { class: "upload-chart__col", "data-bucket": String(i) });
    g.appendChild(
      svgEl("rect", {
        x: snap(x),
        y: top,
        width: snap(slot),
        height: innerH,
        class: "upload-chart__hit",
        "data-bucket": String(i),
      }),
    );
    const gx = snap(cx + cs / 2) + 0.5 / d;
    g.appendChild(svgEl("line", { x1: gx, x2: gx, y1: top, y2: baseY, class: "upload-chart__guide" }));

    // Состояние каждой клетки снизу вверх.
    const state: string[] = new Array(rows).fill("bg");
    const sampled = bucketSampled(b);
    if (!sampled) state.fill("na");
    else {
      const stack = wanOrder.map((id) => ({ id, v: b.wan[id] ?? 0 })).filter((s) => s.v > 0);
      const total = stack.reduce((s, x2) => s + x2.v, 0);
      const cells = total > 0 ? Math.min(rows, Math.max(1, Math.round((total / max) * rows))) : 0;
      const lo = cells / rows < 0.35 ? "-lo" : "";
      let used = 0;
      let acc = 0;
      stack.forEach((seg, si) => {
        acc += seg.v;
        const upto = si === stack.length - 1 ? cells : Math.round((acc / total) * cells);
        const pal = seg.id === "wan2" ? "wan2" : "wan1";
        for (let r = used; r < upto; r++) state[r] = pal + lo;
        used = Math.max(used, upto);
      });
    }
    for (let r = 0; r < rows; r++) {
      const s = state[r];
      const cls =
        s === "bg"
          ? "upload-cell upload-cell--bg"
          : s === "na"
            ? "upload-cell upload-cell--na"
            : `upload-cell upload-cell--${s.slice(0, 4)}${s.endsWith("-lo") ? " upload-cell--lo" : ""} upload-chart__bar`;
      const attrs: Record<string, string | number> = {
        x: cx,
        y: baseY - (r + 1) * u + gap,
        width: cs,
        height: cs,
        rx,
        class: cls,
        "data-cell": `${i}|${r}`,
        "data-state": s,
      };
      if (s === "na") attrs.fill = "url(#upload-unsampled)";
      g.appendChild(svgEl("rect", attrs));
    }
    colsWrap.appendChild(g);

    const label = bucketLabel(b.t, history.period, i, n);
    if (label) {
      const t = svgEl("text", {
        x: (x + slot / 2).toFixed(2),
        y: H - 6,
        "text-anchor": "middle",
        class: "upload-chart__tick",
      });
      t.textContent = label;
      g.appendChild(t);
    }
  });
  return root;
}

/** Снимок цветов клеток (для плавной смены при фоновом обновлении). */
export function farmCellFills(svg: SVGSVGElement | null): Map<string, string> {
  const out = new Map<string, string>();
  if (!svg) return out;
  for (const r of svg.querySelectorAll<SVGRectElement>("rect[data-cell]")) {
    out.set(r.dataset.cell!, `${r.dataset.state}|${getComputedStyle(r).fill}`);
  }
  return out;
}

/** Фоновое обновление: изменившиеся клетки плавно меняют цвет на месте (CSS
 *  transition 0.6 с), геометрия не двигается — края остаются на пикселях. */
export function morphFarmCells(prev: Map<string, string>, next: SVGSVGElement): void {
  if (prev.size === 0) return;
  if (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const changed: SVGRectElement[] = [];
  for (const r of next.querySelectorAll<SVGRectElement>("rect[data-cell]")) {
    const old = prev.get(r.dataset.cell!);
    if (!old) continue;
    const bar = old.indexOf("|");
    if (old.slice(0, bar) === r.dataset.state) continue;
    r.style.transition = "none";
    r.style.fill = old.slice(bar + 1);
    changed.push(r);
  }
  if (!changed.length) return;
  void next.getBoundingClientRect();
  requestAnimationFrame(() => {
    for (const r of changed) {
      r.style.transition = "";
      r.style.fill = "";
    }
  });
}

export function renderMiniChart(
  history: UploadedHistory,
  wanId: string,
  color: string,
  patternId = "upload-unsampled-mini",
): SVGSVGElement {
  const W = 320;
  const H = 120;
  const pad = { l: 38, r: 6, t: 8, b: 20 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  const n = Math.max(1, history.buckets.length);
  const gap = n > 20 ? 1.2 : 2.4;
  const barW = Math.max(3, (innerW - gap * (n - 1)) / n);
  const values = history.buckets.map((b) => (bucketSampled(b) ? (b.wan[wanId] ?? 0) : 0));
  const max = Math.max(1, ...values);
  const peak = Math.max(0, ...values);

  const root = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "upload-chart upload-chart--mini",
    role: "img",
    "aria-label": `Отдача ${wanName(wanId)} за период`,
  }) as SVGSVGElement;
  const defs = svgEl("defs");
  const pat = svgEl("pattern", {
    id: patternId,
    width: 4,
    height: 4,
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(35)",
  });
  pat.appendChild(
    svgEl("path", {
      d: "M0 0 H4",
      stroke: "currentColor",
      "stroke-width": 1,
      class: "upload-chart__hatch",
    }),
  );
  defs.appendChild(pat);
  root.appendChild(defs);

  root.appendChild(
    svgEl("rect", {
      x: pad.l,
      y: pad.t,
      width: innerW,
      height: innerH,
      class: "upload-chart__plot",
    }),
  );

  for (const frac of [0, 0.5, 1]) {
    const y = pad.t + innerH * (1 - frac);
    root.appendChild(
      svgEl("line", {
        x1: pad.l,
        x2: W - pad.r,
        y1: y.toFixed(2),
        y2: y.toFixed(2),
        class: "upload-chart__grid",
      }),
    );
    const lab = svgEl("text", {
      x: pad.l - 5,
      y: (y + 3).toFixed(2),
      "text-anchor": "end",
      class: "upload-chart__axis",
    });
    lab.textContent = frac === 0 ? "0" : fmtChartBytes(max * frac);
    root.appendChild(lab);
  }

  history.buckets.forEach((b, i) => {
    const x = pad.l + i * (barW + gap);
    const v = b.wan[wanId] ?? 0;
    const peakCls = bucketSampled(b) && peak > 0 && v === peak ? " upload-chart__col--peak" : "";
    const g = svgEl("g", { class: `upload-chart__col${peakCls}`, "data-bucket": String(i) });
    g.appendChild(
      svgEl("rect", {
        x: x.toFixed(2),
        y: pad.t,
        width: barW.toFixed(2),
        height: innerH,
        class: "upload-chart__hit",
        "data-bucket": String(i),
      }),
    );

    if (!bucketSampled(b)) {
      g.appendChild(
        svgEl("rect", {
          x: x.toFixed(2),
          y: pad.t,
          width: barW.toFixed(2),
          height: innerH,
          fill: `url(#${patternId})`,
          class: "upload-chart__empty",
        }),
      );
    } else if (v <= 0) {
      g.appendChild(
        svgEl("rect", {
          x: x.toFixed(2),
          y: (pad.t + innerH - 2).toFixed(2),
          width: barW.toFixed(2),
          height: 2,
          fill: color,
          class: "upload-chart__zero",
        }),
      );
    } else {
      const h = (v / max) * innerH;
      g.appendChild(
        svgEl("rect", {
          x: x.toFixed(2),
          y: (pad.t + innerH - h).toFixed(2),
          width: barW.toFixed(2),
          height: Math.max(0.5, h).toFixed(2),
          fill: color,
          rx: 2,
          class: "upload-chart__bar",
        }),
      );
    }

    const label = bucketLabel(b.t, history.period, i, n);
    if (label) {
      const t = svgEl("text", {
        x: (x + barW / 2).toFixed(2),
        y: H - 5,
        "text-anchor": "middle",
        class: "upload-chart__tick",
      });
      t.textContent = label;
      g.appendChild(t);
    }
    root.appendChild(g);
  });
  return root;
}

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
