/* Общий рендер столбиковых графиков «квадратиками» (1.61.0).
 *
 * Раскладка — в CSS-пикселях реальной ширины контейнера: viewBox == размер
 * элемента, без растяжения; клетка и зазор кратны физическому пикселю
 * (round(n·dpr)/dpr), сцена сдвигается на дробную долю пикселя позиции svg —
 * края клеток чёткие при DPR 1 / 1.25 / 1.5 / 2. Клетки — отдельные rect
 * (паттерны браузер ресэмплит). Если клетки выходят мельче minCell, график
 * становится шире контейнера и листается по горизонтали. */

const NS = "http://www.w3.org/2000/svg";

function svgEl(name: string, attrs: Record<string, string | number> = {}): SVGElement {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
}

export type CellCol = {
  sampled: boolean;
  /** Сегменты снизу вверх; id — класс палитры (wan1 / wan2). */
  segs: { id: string; v: number }[];
  label?: string;
  peak?: boolean;
};

export type CellChartSpec = {
  width: number;
  height: number;
  dpr: number;
  pad: { l: number; r: number; t: number; b: number };
  cols: CellCol[];
  max: number;
  axisFmt: (v: number) => string;
  /** Предел клетки (CSS px) и минимум, ниже которого график листается. */
  maxCell: number;
  minCell: number;
  gap?: number;
  cls: string;
  uid: string;
  aria: string;
};

let uidSeq = 0;

export function renderCellChart(spec: CellChartSpec): SVGSVGElement {
  const d = Math.max(1, spec.dpr || 1);
  const snap = (v: number) => Math.round(v * d) / d;
  const { pad, cols } = spec;
  const n = Math.max(1, cols.length);
  const gap = snap(spec.gap ?? 2);
  // Минимальная ширина, при которой клетка не мельче minCell.
  const minW = pad.l + pad.r + n * (spec.minCell + gap + 1);
  const W = Math.max(Math.round(spec.width), Math.ceil(minW));
  const H = Math.round(spec.height);
  const innerW = W - pad.l - pad.r;
  const slot = innerW / n;
  const cs = Math.max(snap(spec.minCell), snap(Math.min(spec.maxCell, slot - Math.max(gap + 1, slot * 0.2))));
  const u = cs + gap;
  const baseY = H - pad.b;
  const rows = Math.max(1, Math.floor((baseY - pad.t) / u));
  const innerH = rows * u;
  const top = baseY - innerH;
  const rx = cs >= 10 ? 2 : cs >= 6 ? 1.5 : 1;
  const id = `${spec.uid}-${++uidSeq}`;

  const root = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    width: W,
    height: H,
    class: `upload-chart upload-chart--cells ${spec.cls}`,
    role: "img",
    "aria-label": spec.aria,
  }) as SVGSVGElement;
  root.style.width = `${W}px`;
  root.style.height = `${H}px`;

  const defs = svgEl("defs");
  const pat = svgEl("pattern", {
    id: `${id}-na`,
    width: 5,
    height: 5,
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(35)",
  });
  pat.appendChild(svgEl("path", { d: "M0 0 H5", stroke: "currentColor", "stroke-width": 1, class: "upload-chart__hatch" }));
  defs.appendChild(pat);
  const reveal = svgEl("clipPath", { id: `${id}-reveal` });
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
  const stage = svgEl("g", { class: "upload-chart__stage" });
  root.appendChild(stage);

  for (const frac of [0, 0.5, 1]) {
    const y = snap(top + innerH * (1 - frac)) + 0.5 / d;
    stage.appendChild(svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: "upload-chart__grid" }));
    const lab = svgEl("text", { x: pad.l - 5, y: (y + 3).toFixed(2), "text-anchor": "end", class: "upload-chart__axis" });
    lab.textContent = frac === 0 ? "0" : spec.axisFmt(spec.max * frac);
    stage.appendChild(lab);
  }

  const wrap = svgEl("g", { class: "upload-chart__cols", "clip-path": `url(#${id}-reveal)` });
  stage.appendChild(wrap);
  const max = Math.max(1, spec.max);

  cols.forEach((c, i) => {
    const x = pad.l + i * slot;
    const cx = snap(x + (slot - cs) / 2);
    const g = svgEl("g", {
      class: `upload-chart__col${c.peak ? " upload-chart__col--peak" : ""}`,
      "data-bucket": String(i),
    });
    g.appendChild(
      svgEl("rect", { x: snap(x), y: top, width: snap(slot), height: innerH, class: "upload-chart__hit", "data-bucket": String(i) }),
    );
    const gx = snap(cx + cs / 2) + 0.5 / d;
    g.appendChild(svgEl("line", { x1: gx, x2: gx, y1: top, y2: baseY, class: "upload-chart__guide" }));

    const state: string[] = new Array(rows).fill("bg");
    if (!c.sampled) state.fill("na");
    else {
      const segs = c.segs.filter((s) => s.v > 0);
      const total = segs.reduce((s, x2) => s + x2.v, 0);
      const cells = total > 0 ? Math.min(rows, Math.max(1, Math.round((total / max) * rows))) : 0;
      const lo = cells / rows < 0.35 ? "-lo" : "";
      let used = 0;
      let acc = 0;
      segs.forEach((seg, si) => {
        acc += seg.v;
        const upto = si === segs.length - 1 ? cells : Math.round((acc / total) * cells);
        for (let r = used; r < upto; r++) state[r] = seg.id + lo;
        used = Math.max(used, upto);
      });
    }
    for (let r = 0; r < rows; r++) {
      const s = state[r];
      let cls = "upload-cell upload-cell--bg";
      if (s === "na") cls = "upload-cell upload-cell--na";
      else if (s !== "bg") {
        const lo = s.endsWith("-lo");
        cls = `upload-cell upload-cell--${lo ? s.slice(0, -3) : s}${lo ? " upload-cell--lo" : ""} upload-chart__bar`;
      }
      const a: Record<string, string | number> = {
        x: cx,
        y: baseY - (r + 1) * u + gap,
        width: cs,
        height: cs,
        rx,
        class: cls,
        "data-cell": `${i}|${r}`,
        "data-state": s,
      };
      if (s === "na") a.fill = `url(#${id}-na)`;
      g.appendChild(svgEl("rect", a));
    }
    wrap.appendChild(g);
    if (c.label) {
      const t = svgEl("text", { x: (x + slot / 2).toFixed(2), y: H - 6, "text-anchor": "middle", class: "upload-chart__tick" });
      t.textContent = c.label;
      g.appendChild(t);
    }
  });
  return root;
}

/** Сдвиг сцены на дробную долю физического пикселя позиции svg на странице. */
export function alignCellStage(svg: SVGSVGElement): void {
  const st = svg.querySelector<SVGGElement>(".upload-chart__stage");
  if (!st || !svg.isConnected) return;
  st.removeAttribute("transform");
  const dpr = window.devicePixelRatio || 1;
  const b = svg.getBoundingClientRect();
  const fx = (b.left * dpr) % 1;
  const fy = (b.top * dpr) % 1;
  const dx = fx > 0.01 && fx < 0.99 ? -fx / dpr : 0;
  const dy = fy > 0.01 && fy < 0.99 ? -fy / dpr : 0;
  if (dx || dy) st.setAttribute("transform", `translate(${dx.toFixed(4)} ${dy.toFixed(4)})`);
}

export function reducedMotionOn(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Заполнение снизу вверх во всех колонках одновременно: общий clipPath, один
 *  rAF, 0.95 с easeOutQuint; край — на физическом пикселе, в конце клип полный.
 *  Возвращает отмену (сразу итог). */
export function growCells(svg: SVGSVGElement, dur = 950): () => void {
  const r = svg.querySelector<SVGRectElement>(".upload-chart__reveal");
  if (!r || reducedMotionOn()) return () => {};
  const base = Number(r.getAttribute("data-base"));
  const H = Number(r.getAttribute("height"));
  const d = Number(r.getAttribute("data-dpr")) || 1;
  const ease = (t: number) => 1 - Math.pow(1 - t, 5);
  const set = (p: number) => {
    const y = p >= 1 ? 0 : Math.round(base * (1 - p) * d) / d;
    r.setAttribute("y", String(y));
    r.setAttribute("height", String(H - y));
  };
  set(0);
  const t0 = performance.now();
  let raf = 0;
  const step = (now: number) => {
    const t = Math.min(1, (now - t0) / dur);
    set(ease(t));
    raf = t < 1 ? requestAnimationFrame(step) : 0;
  };
  raf = requestAnimationFrame(step);
  return () => {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    set(1);
  };
}

/** Снимок цветов клеток — для плавной смены при фоновом обновлении. */
export function cellFills(svg: SVGSVGElement | null): Map<string, string> {
  const out = new Map<string, string>();
  if (!svg) return out;
  for (const r of svg.querySelectorAll<SVGRectElement>("rect[data-cell]")) {
    out.set(r.dataset.cell!, `${r.dataset.state}|${getComputedStyle(r).fill}`);
  }
  return out;
}

/** Изменившиеся клетки плавно меняют цвет на месте (CSS transition 0.6 с). */
export function morphCells(prev: Map<string, string>, next: SVGSVGElement): void {
  if (prev.size === 0 || reducedMotionOn()) return;
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

/** Следит за шириной контейнера: draw() при первом появлении размера и при
 *  каждом изменении ширины / DPR. Возвращает отписку. */
export function watchCellBox(box: HTMLElement, draw: (first: boolean) => void): () => void {
  let lastW = -1;
  let lastDpr = 0;
  let raf = 0;
  let first = true;
  const check = () => {
    const w = box.clientWidth;
    if (w <= 0) return;
    if (Math.abs(w - lastW) < 1 && window.devicePixelRatio === lastDpr) return;
    lastW = w;
    lastDpr = window.devicePixelRatio;
    const f = first;
    first = false;
    draw(f);
  };
  if (typeof ResizeObserver !== "function") {
    requestAnimationFrame(check);
    return () => {};
  }
  const ro = new ResizeObserver(() => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(check);
  });
  ro.observe(box);
  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
  };
}
