/**
 * Фон входа — «потоки данных»: бледные кривые сходятся в синий узел-реле
 * и расходятся к пирам, по ним бегут точки с хвостами.
 *
 * Бережём процессор: кривые рисуются один раз во внеэкранный холст, кадров
 * не больше 30 в секунду, DPR не выше 1.5, цикл стоит, пока вкладка скрыта
 * или панель вне экрана. При prefers-reduced-motion — один неподвижный кадр.
 */

const INK = "71,85,105";
const BLUE = "37,99,235";
const FRAME_MS = 32;
const MAX_DPR = 1.5;

type P = { x: number; y: number };
type Curve = [P, P, P, P];
type Dot = { ci: number; u: number; sp: number; blue: boolean };
type Scene = { curves: Curve[]; bg: HTMLCanvasElement; dots: Dot[]; hub: P };

function bez(q: Curve, u: number): [number, number] {
  const v = 1 - u;
  const a = v * v * v, b = 3 * v * v * u, c = 3 * v * u * u, d = u * u * u;
  return [a * q[0].x + b * q[1].x + c * q[2].x + d * q[3].x, a * q[0].y + b * q[1].y + c * q[2].y + d * q[3].y];
}

function buildScene(w: number, h: number, dpr: number): Scene {
  const hub = { x: w * 0.62, y: h * 0.5 };
  const curves: Curve[] = [];
  for (let k = 0; k < 9; k++) {
    const sy = h * (0.08 + k * 0.105);
    curves.push([{ x: -20, y: sy }, { x: w * 0.3, y: sy }, { x: hub.x - w * 0.2, y: hub.y + (sy - hub.y) * 0.3 }, hub]);
  }
  for (let k = 0; k < 5; k++) {
    const ey = h * (0.15 + k * 0.18);
    curves.push([hub, { x: hub.x + w * 0.15, y: hub.y + (ey - hub.y) * 0.4 }, { x: w * 0.85, y: ey }, { x: w + 20, y: ey }]);
  }
  const bg = document.createElement("canvas");
  bg.width = Math.max(1, Math.round(w * dpr));
  bg.height = Math.max(1, Math.round(h * dpr));
  const b = bg.getContext("2d")!;
  b.scale(dpr, dpr);
  b.lineWidth = 1;
  b.strokeStyle = `rgba(${INK},.18)`;
  for (const q of curves) {
    b.beginPath();
    b.moveTo(q[0].x, q[0].y);
    b.bezierCurveTo(q[1].x, q[1].y, q[2].x, q[2].y, q[3].x, q[3].y);
    b.stroke();
  }
  const dots: Dot[] = [];
  curves.forEach((_, ci) => {
    for (let m = 0; m < 4; m++) dots.push({ ci, u: Math.random(), sp: 0.12 + Math.random() * 0.12, blue: Math.random() < 0.3 });
  });
  return { curves, bg, dots, hub };
}

function draw(c: CanvasRenderingContext2D, w: number, h: number, s: Scene, dt: number, t: number): void {
  c.clearRect(0, 0, w, h);
  c.drawImage(s.bg, 0, 0, w, h);
  for (const d of s.dots) {
    d.u = (d.u + d.sp * dt) % 1;
    const q = s.curves[d.ci];
    for (let k = 0; k < 6; k++) {
      const [x, y] = bez(q, Math.max(0, d.u - k * 0.008));
      c.fillStyle = d.blue ? `rgba(${BLUE},${0.8 - k * 0.13})` : `rgba(${INK},${0.5 - k * 0.08})`;
      c.beginPath();
      c.arc(x, y, (d.blue ? 2 : 1.5) - k * 0.2, 0, 7);
      c.fill();
    }
  }
  const r = 6 + 2 * Math.sin(t * 2);
  c.strokeStyle = `rgba(${BLUE},.35)`;
  c.lineWidth = 1;
  c.beginPath();
  c.arc(s.hub.x, s.hub.y, r + 8, 0, 7);
  c.stroke();
  c.fillStyle = `rgb(${BLUE})`;
  c.beginPath();
  c.arc(s.hub.x, s.hub.y, 4, 0, 7);
  c.fill();
}

export function loginStreams(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "login-streams";
  wrap.setAttribute("aria-hidden", "true");
  const canvas = document.createElement("canvas");
  wrap.append(canvas);
  const ctx = canvas.getContext("2d");
  if (!ctx) return wrap;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let w = 0, h = 0, raf = 0, last = 0, t = 0, running = false, onScreen = true;
  let scene: Scene | null = null;

  const frame = (ts: number) => {
    if (!wrap.isConnected) return stop(true);
    raf = requestAnimationFrame(frame);
    if (ts - last < FRAME_MS) return;
    const dt = Math.min((ts - last) / 1000, 0.1);
    last = ts;
    t += dt;
    if (scene) draw(ctx, w, h, scene, dt, t);
  };
  function go(): void {
    if (running || reduce || document.hidden || !onScreen || !scene) return;
    running = true;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  function stop(teardown = false): void {
    running = false;
    cancelAnimationFrame(raf);
    if (teardown) {
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    }
  }
  const onVis = () => (document.hidden ? stop() : go());
  function resize(): void {
    const r = canvas.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    w = r.width;
    h = r.height;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    scene = buildScene(w, h, dpr);
    draw(ctx!, w, h, scene, 0, t);
    go();
  }
  const io = new IntersectionObserver((e) => {
    onScreen = e[e.length - 1].isIntersecting;
    onScreen ? go() : stop();
  });
  const ro = new ResizeObserver(resize);
  io.observe(canvas);
  ro.observe(canvas);
  document.addEventListener("visibilitychange", onVis);
  return wrap;
}
