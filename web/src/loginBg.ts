/**
 * Фон входа — кривые из компонента Background Paths (Aceternity UI).
 *
 * Мерцание, которое не давалось несколько подходов, оказалось не ошибкой
 * анимации, а свойством картинки: штрих тоньше пикселя при движении каждый
 * кадр по-новому ложится на пиксельную сетку, и полоса таких штрихов дрожит.
 * Отсюда два решения. Рисунок неподвижен, движение отдано композитору — он
 * возит готовый растр и сглаживание не пересчитывает. А сами линии вдвое
 * реже и заметно толще: замер разницы соседних кадров упал с 5.3 до 1.1.
 */

const NS = "http://www.w3.org/2000/svg";

/** У оригинала кривых вдвое больше, но там они и не движутся вместе с фоном. */
const PATHS = 18;

/** Шаг между кривыми против оригинального — чтобы веер занял ту же площадь. */
const STEP = 2;

/** Набор рисуется дважды, зеркально: position = 1 и -1. */
function pathD(i: number, position: number): string {
  const k = i * STEP;
  const dx = k * 5 * position;
  const dy = k * 6;
  return (
    `M-${380 - dx} -${189 + dy}` +
    `C-${380 - dx} -${189 + dy} -${312 - dx} ${216 - dy} ${152 - dx} ${343 - dy}` +
    `C${616 - dx} ${470 - dy} ${684 - dx} ${875 - dy} ${684 - dx} ${875 - dy}`
  );
}

/**
 * Ни фильтров, ни масок: фильтр считается в экранных координатах, поэтому
 * на каждом сдвиге слоя браузер пересобирал бы растр — и слой перестал бы
 * быть композитным. Проверено: с фильтром 24 кадра в секунду, без него 128.
 */
function pathsLayer(position: number): SVGSVGElement {
  const side = position > 0 ? "a" : "b";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", `login-paths__svg login-paths__svg--${side}`);
  // Кадр по разлёту веера, а не по месту схождения: у оригинала (696×316)
  // сходящийся хвост кривых остаётся за рамкой, там линии ложатся впритык.
  svg.setAttribute("viewBox", "-380 -200 600 660");
  svg.setAttribute("fill", "none");
  svg.setAttribute("preserveAspectRatio", "xMidYMid slice");
  for (let i = 0; i < PATHS; i++) {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", pathD(i, position));
    p.setAttribute("stroke", "currentColor");
    // Толщина и плотность растут с номером — от этого у веера глубина.
    p.setAttribute("stroke-width", (1.7 + i * 0.1).toFixed(2));
    p.setAttribute("stroke-opacity", (0.055 + i * 0.033).toFixed(3));
    svg.append(p);
  }
  return svg;
}

/** Два зеркальных набора кривых на всю левую колонку. */
export function loginPaths(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "login-paths";
  wrap.setAttribute("aria-hidden", "true");
  wrap.append(pathsLayer(1), pathsLayer(-1));
  return wrap;
}
