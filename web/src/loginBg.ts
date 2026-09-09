/** Фон входа: плавающие пути как у Efferd Auth Page, без framer-motion. */

import { reducedMotion } from "./motionPop";

function pathD(i: number, position: number): string {
  const x = 380 - i * 5 * position;
  const y = 189 + i * 6;
  return (
    `M-${x} -${y}` +
    `C-${x} -${y} -${312 - i * 5 * position} ${216 - i * 6} ${152 - i * 5 * position} ${343 - i * 6}` +
    `C${616 - i * 5 * position} ${470 - i * 6} ${684 - i * 5 * position} ${875 - i * 6} ${684 - i * 5 * position} ${875 - i * 6}`
  );
}

function pathsLayer(position: number): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "login-paths__svg");
  svg.setAttribute("viewBox", "0 0 696 316");
  svg.setAttribute("fill", "none");
  svg.setAttribute("preserveAspectRatio", "xMidYMid slice");
  const fid = position > 0 ? "login-path-soft-a" : "login-path-soft-b";
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  const filter = document.createElementNS("http://www.w3.org/2000/svg", "filter");
  filter.setAttribute("id", fid);
  filter.setAttribute("x", "-10%");
  filter.setAttribute("y", "-10%");
  filter.setAttribute("width", "120%");
  filter.setAttribute("height", "120%");
  const blur = document.createElementNS("http://www.w3.org/2000/svg", "feGaussianBlur");
  blur.setAttribute("in", "SourceGraphic");
  blur.setAttribute("stdDeviation", "0.4");
  filter.append(blur);
  defs.append(filter);
  svg.append(defs);
  const n = reducedMotion() ? 13 : 32;
  for (let i = 0; i < n; i++) {
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", pathD(i, position));
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-width", (0.22 + i * 0.01).toFixed(2));
    p.setAttribute("filter", `url(#${fid})`);
    p.style.setProperty("--i", String(i));
    svg.append(p);
  }
  return svg;
}

/** Два слоя кривых на весь экран. */
export function loginPaths(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "login-paths";
  wrap.setAttribute("aria-hidden", "true");
  wrap.append(pathsLayer(1), pathsLayer(-1));
  return wrap;
}
