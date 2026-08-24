/** SVG-графики отдачи на /network/uploaded. Ось, сетка, наведение, штрих «нет данных». */

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

export function renderFarmChart(history: UploadedHistory): SVGSVGElement {
  const W = 680;
  const H = 200;
  const pad = { l: 46, r: 8, t: 10, b: 24 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  const n = Math.max(1, history.buckets.length);
  const gap = n > 20 ? 1.5 : 3;
  const barW = Math.max(3, (innerW - gap * (n - 1)) / n);
  const wanOrder = uniqueWans(history);
  const sampledVals = history.buckets.filter(bucketSampled).map((b) =>
    wanOrder.reduce((s, id) => s + (b.wan[id] ?? 0), 0),
  );
  const max = Math.max(1, ...sampledVals);

  const root = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "upload-chart",
    role: "img",
    "aria-label": "Отдача фермы за период",
  }) as SVGSVGElement;

  const defs = svgEl("defs");
  const pat = svgEl("pattern", {
    id: "upload-unsampled",
    width: 5,
    height: 5,
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(35)",
  });
  pat.appendChild(
    svgEl("path", {
      d: "M0 0 H5",
      stroke: "currentColor",
      "stroke-width": 1,
      class: "upload-chart__hatch",
    }),
  );
  defs.appendChild(pat);
  root.appendChild(defs);

  const plot = svgEl("rect", {
    x: pad.l,
    y: pad.t,
    width: innerW,
    height: innerH,
    class: "upload-chart__plot",
  });
  root.appendChild(plot);

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
      x: pad.l - 6,
      y: (y + 3).toFixed(2),
      "text-anchor": "end",
      class: "upload-chart__axis",
    });
    lab.textContent = frac === 0 ? "0" : fmtChartBytes(max * frac);
    root.appendChild(lab);
  }

  history.buckets.forEach((b, i) => {
    const x = pad.l + i * (barW + gap);
    const g = svgEl("g", { class: "upload-chart__col", "data-bucket": String(i) });
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
          fill: "url(#upload-unsampled)",
          class: "upload-chart__empty",
        }),
      );
    } else {
      let y = pad.t + innerH;
      const stack = wanOrder.map((id) => ({ id, v: b.wan[id] ?? 0 })).filter((s) => s.v > 0);
      if (stack.length === 0) {
        g.appendChild(
          svgEl("rect", {
            x: x.toFixed(2),
            y: (pad.t + innerH - 2).toFixed(2),
            width: barW.toFixed(2),
            height: 2,
            class: "upload-chart__zero",
          }),
        );
      } else {
        stack.forEach((seg, si) => {
          const h = (seg.v / max) * innerH;
          y -= h;
          const isTop = si === stack.length - 1;
          g.appendChild(
            svgEl("rect", {
              x: x.toFixed(2),
              y: y.toFixed(2),
              width: barW.toFixed(2),
              height: Math.max(0.5, h).toFixed(2),
              fill: wanColor(seg.id),
              rx: isTop ? 2 : 0,
              class: `upload-chart__bar upload-chart__bar--${seg.id}`,
            }),
          );
        });
      }
    }

    const label = bucketLabel(b.t, history.period, i, n);
    if (label) {
      const t = svgEl("text", {
        x: (x + barW / 2).toFixed(2),
        y: H - 6,
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

export function renderMiniChart(
  values: number[],
  color: string,
  sampled?: boolean[],
  patternId = "upload-unsampled-mini",
): SVGSVGElement {
  const W = 240;
  const H = 56;
  const pad = 2;
  const n = Math.max(1, values.length);
  const gap = 1.2;
  const barW = Math.max(1.5, (W - pad * 2 - gap * (n - 1)) / n);
  const live = values.map((v, i) => (sampled && sampled[i] === false ? 0 : v));
  const max = Math.max(1, ...live);
  const root = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "upload-chart upload-chart--mini",
    "aria-hidden": "true",
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

  values.forEach((v, i) => {
    const x = pad + i * (barW + gap);
    if (sampled && sampled[i] === false) {
      root.appendChild(
        svgEl("rect", {
          x: x.toFixed(2),
          y: pad,
          width: barW.toFixed(2),
          height: H - pad * 2,
          fill: `url(#${patternId})`,
          class: "upload-chart__empty",
        }),
      );
      return;
    }
    const h = (Math.max(0, v) / max) * (H - pad * 2);
    if (h <= 0) {
      root.appendChild(
        svgEl("rect", {
          x: x.toFixed(2),
          y: H - pad - 2,
          width: barW.toFixed(2),
          height: 2,
          fill: color,
          opacity: 0.35,
        }),
      );
      return;
    }
    root.appendChild(
      svgEl("rect", {
        x: x.toFixed(2),
        y: (H - pad - h).toFixed(2),
        width: barW.toFixed(2),
        height: h.toFixed(2),
        fill: color,
        rx: 1,
      }),
    );
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

    const host = tip.offsetParent as HTMLElement | null;
    const box = (host ?? tip.parentElement)?.getBoundingClientRect();
    const x = ev.clientX - (box?.left ?? 0) + 12;
    const y = ev.clientY - (box?.top ?? 0) + 12;
    const maxX = (box?.width ?? 320) - tip.offsetWidth - 8;
    tip.style.left = `${Math.max(8, Math.min(x, maxX))}px`;
    tip.style.top = `${Math.max(8, y)}px`;
    tip.hidden = false;
  };

  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", hide);
  return () => {
    svg.removeEventListener("pointermove", move);
    svg.removeEventListener("pointerleave", hide);
    hide();
  };
}
