/** SVG-графики отдачи на /network/uploaded. Без Chart.js: столбики + стек WAN. */

export type HistoryPeriod = "day" | "week" | "month";

export const HISTORY_PERIODS: { id: HistoryPeriod; label: string }[] = [
  { id: "day", label: "День" },
  { id: "week", label: "Неделя" },
  { id: "month", label: "Месяц" },
];

export type UploadedHistory = {
  period: HistoryPeriod;
  buckets: { t: string; farm: number; wan: Record<string, number> }[];
  total: { farm: number; wan: Record<string, number> };
  previous_total: { farm: number; wan: Record<string, number> };
};

const NS = "http://www.w3.org/2000/svg";

export function wanColor(id: string): string {
  if (id === "wan1") return "var(--accent)";
  if (id === "wan2") return "var(--success)";
  return "var(--muted)";
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

function svgEl(name: string, attrs: Record<string, string | number> = {}): SVGElement {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
}

function uniqueWans(history: UploadedHistory): string[] {
  const seen = new Set<string>(["wan1", "wan2"]);
  for (const b of history.buckets) {
    for (const id of Object.keys(b.wan)) seen.add(id);
  }
  return [...seen].filter((id) => history.buckets.some((b) => (b.wan[id] ?? 0) > 0 || id === "wan1" || id === "wan2"));
}

function bucketLabel(iso: string, period: HistoryPeriod, index: number, n: number): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  if (period === "day") {
    if (index % 4 !== 0 && index !== n - 1) return "";
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  if (period === "week") {
    return d.toLocaleDateString("ru-RU", { weekday: "short" });
  }
  if (index % 5 !== 0 && index !== n - 1) return "";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export function renderFarmChart(history: UploadedHistory): SVGSVGElement {
  const W = 640;
  const H = 160;
  const pad = { l: 4, r: 4, t: 8, b: 22 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  const n = Math.max(1, history.buckets.length);
  const gap = n > 20 ? 1 : 2;
  const barW = Math.max(2, (innerW - gap * (n - 1)) / n);
  const wanOrder = uniqueWans(history);
  const max = Math.max(
    1,
    ...history.buckets.map((b) => wanOrder.reduce((s, id) => s + (b.wan[id] ?? 0), 0)),
  );

  const root = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "upload-chart",
    role: "img",
    "aria-label": "Отдача фермы за период",
  }) as SVGSVGElement;

  history.buckets.forEach((b, i) => {
    let y = pad.t + innerH;
    for (const id of wanOrder) {
      const v = b.wan[id] ?? 0;
      const h = (v / max) * innerH;
      if (h <= 0) continue;
      y -= h;
      root.appendChild(
        svgEl("rect", {
          x: (pad.l + i * (barW + gap)).toFixed(2),
          y: y.toFixed(2),
          width: barW.toFixed(2),
          height: h.toFixed(2),
          fill: wanColor(id),
          rx: 1,
        }),
      );
    }
    const label = bucketLabel(b.t, history.period, i, n);
    if (!label) return;
    const t = svgEl("text", {
      x: (pad.l + i * (barW + gap) + barW / 2).toFixed(2),
      y: H - 6,
      "text-anchor": "middle",
      class: "upload-chart__tick",
    });
    t.textContent = label;
    root.appendChild(t);
  });
  return root;
}

export function renderMiniChart(values: number[], color: string): SVGSVGElement {
  const W = 240;
  const H = 56;
  const pad = 2;
  const n = Math.max(1, values.length);
  const gap = 1;
  const barW = Math.max(1.5, (W - pad * 2 - gap * (n - 1)) / n);
  const max = Math.max(1, ...values);
  const root = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "upload-chart upload-chart--mini",
    "aria-hidden": "true",
  }) as SVGSVGElement;
  values.forEach((v, i) => {
    const h = (Math.max(0, v) / max) * (H - pad * 2);
    if (h <= 0) return;
    root.appendChild(
      svgEl("rect", {
        x: (pad + i * (barW + gap)).toFixed(2),
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
