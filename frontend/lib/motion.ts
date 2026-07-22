// GSAP motion helpers, all gated behind prefers-reduced-motion.
// Import { prefersReducedMotion, revealStagger, countUp, shake } and call
// inside useGSAP / useEffect so animations clean themselves up.

import { gsap } from "gsap";

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Reveal [data-anim] elements (hidden via globals.css) with a stagger fade-up.
 *  If motion is reduced, just make them visible instantly. */
export function revealStagger(
  scope: HTMLElement | null,
  selector = "[data-anim]",
  opts: { y?: number; stagger?: number; delay?: number } = {}
): void {
  if (!scope) return;
  const targets = scope.querySelectorAll(selector);
  if (!targets.length) return;
  if (prefersReducedMotion()) {
    gsap.set(targets, { opacity: 1, y: 0 });
    return;
  }
  gsap.fromTo(
    targets,
    { opacity: 0, y: opts.y ?? 14 },
    {
      opacity: 1,
      y: 0,
      duration: 0.55,
      ease: "power2.out",
      stagger: opts.stagger ?? 0.07,
      delay: opts.delay ?? 0,
    }
  );
}

/** Tween a number from 0 (or `from`) to `to`, calling `onUpdate` with the
 *  current value each frame. Returns the tween (or null when reduced). */
export function countUp(
  to: number,
  onUpdate: (v: number) => void,
  opts: { from?: number; duration?: number; decimals?: number } = {}
): gsap.core.Tween | null {
  const from = opts.from ?? 0;
  if (prefersReducedMotion() || to === from) {
    onUpdate(to);
    return null;
  }
  const state = { v: from };
  return gsap.to(state, {
    v: to,
    duration: opts.duration ?? 0.9,
    ease: "power2.out",
    onUpdate: () => onUpdate(state.v),
  });
}

/** Attention shake + red flash for the mandate-refusal moment. */
export function shake(el: HTMLElement | null): void {
  if (!el) return;
  if (prefersReducedMotion()) return;
  gsap
    .timeline()
    .to(el, { x: -6, duration: 0.06 })
    .to(el, { x: 6, duration: 0.06 })
    .to(el, { x: -4, duration: 0.06 })
    .to(el, { x: 4, duration: 0.06 })
    .to(el, { x: 0, duration: 0.06 });
}

/** Deal-in: a card scales/rotates into place when issued. */
export function dealIn(el: HTMLElement | null): void {
  if (!el) return;
  if (prefersReducedMotion()) {
    gsap.set(el, { opacity: 1, scale: 1, rotate: 0, y: 0 });
    return;
  }
  gsap.fromTo(
    el,
    { opacity: 0, scale: 0.92, rotate: -3, y: 16 },
    { opacity: 1, scale: 1, rotate: 0, y: 0, duration: 0.6, ease: "back.out(1.5)" }
  );
}
