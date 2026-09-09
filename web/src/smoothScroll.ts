/** Плавная прокрутка страницы (Lenis), как у референса Aristide Benoist. */

import Lenis from "lenis";
import "lenis/dist/lenis.css";

let lenis: Lenis | null = null;
let modalLocks = 0;

function overlayNode(node: HTMLElement): boolean {
  return Boolean(
    node.closest(
      ".modal-overlay, .cselect-panel, .tray-menu, .search-palette__results, .login-window",
    ),
  );
}

export function startSmoothScroll(): void {
  if (lenis) return;
  lenis = new Lenis({
    autoRaf: true,
    autoToggle: true,
    anchors: true,
    allowNestedScroll: true,
    stopInertiaOnNavigate: true,
    lerp: 0.08,
    wheelMultiplier: 0.92,
    prevent: overlayNode,
  });
}

export function refreshSmoothScroll(): void {
  lenis?.resize();
}

export function scrollToTop(): void {
  if (lenis) {
    lenis.scrollTo(0, { lerp: 0.08 });
    return;
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

export function lockPageScroll(): void {
  modalLocks += 1;
  lenis?.stop();
}

export function unlockPageScroll(): void {
  modalLocks = Math.max(0, modalLocks - 1);
  if (modalLocks === 0) lenis?.start();
}
