"use client";

// laeria — cinematic landing ("The Instrument").
// Higgsfield-generated hero still + GSAP/Lenis scroll-film motion.
// The whole page reads as one continuous instrument: value on a rail,
// gated by a mandate. Ends by leading into the app.

import { useEffect, useRef } from "react";
import Link from "next/link";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";

const HEAD_WORDS = ["An", "agent", "that", "pays", "—", "under", "a", "mandate."];

const STEPS = [
  { n: "01", k: "Fund", d: "Stablecoins move into a KYC-ready account. The agent starts with a balance, not a blank cheque." },
  { n: "02", k: "Discover", d: "It scans the live storefront in a real browser and re-reads the price a human would actually be charged." },
  { n: "03", k: "Issue", d: "A single-use virtual card is minted, its limit pinned to this one purchase. Nothing else can be spent on it." },
  { n: "04", k: "Execute", d: "Checkout completes, the card is destroyed, and every number lands in an audit trail." },
];

export default function Landing() {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      gsap.set("[data-anim]", { opacity: 1, y: 0 });
      return;
    }

    gsap.registerPlugin(ScrollTrigger);
    const lenis = new Lenis({ lerp: 0.1, wheelMultiplier: 1 });
    lenis.on("scroll", ScrollTrigger.update);
    const ticker = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(ticker);
    gsap.ticker.lagSmoothing(0);

    const ctx = gsap.context((self) => {
      const q = self.selector!;

      // ---- Hero entrance ----
      const tl = gsap.timeline({ defaults: { ease: "power3.out" } });
      tl.from(q(".hero-eyebrow"), { opacity: 0, y: 12, duration: 0.6 })
        .from(q(".hero-word"), { opacity: 0, y: 26, duration: 0.7, stagger: 0.06 }, "-=0.2")
        .from(q(".hero-sub"), { opacity: 0, y: 14, duration: 0.6 }, "-=0.3")
        .from(q(".hero-cta"), { opacity: 0, y: 10, duration: 0.5 }, "-=0.3")
        .fromTo(
          q(".hero-plate"),
          { opacity: 0, scale: 1.08, xPercent: 6 },
          { opacity: 1, scale: 1, xPercent: 0, duration: 1.3, ease: "power2.out" },
          0.1
        )
        .from(q(".hero-cue"), { opacity: 0, duration: 0.6 }, "-=0.2");

      // light sweep travels the rail forever (ambient)
      gsap.to(q(".sweep"), {
        xPercent: 220,
        duration: 4.5,
        ease: "power1.inOut",
        repeat: -1,
        repeatDelay: 1.2,
      });

      // hero image parallax on scroll
      gsap.to(q(".hero-plate"), {
        yPercent: 14,
        ease: "none",
        scrollTrigger: { trigger: q(".hero"), start: "top top", end: "bottom top", scrub: true },
      });
      gsap.to(q(".hero-copy"), {
        yPercent: -18,
        opacity: 0.15,
        ease: "none",
        scrollTrigger: { trigger: q(".hero"), start: "top top", end: "bottom top", scrub: true },
      });

      // ---- Thesis reveal ----
      gsap.utils.toArray<HTMLElement>(q(".reveal")).forEach((el) => {
        gsap.from(el, {
          opacity: 0,
          y: 24,
          duration: 0.8,
          ease: "power2.out",
          scrollTrigger: { trigger: el, start: "top 82%" },
        });
      });

      // ---- Lifecycle: pinned, steps advance with scroll ----
      const steps = gsap.utils.toArray<HTMLElement>(q(".step"));
      const rail = q(".rail-fill")[0] as HTMLElement;
      const pinTl = gsap.timeline({
        scrollTrigger: {
          trigger: q(".lifecycle"),
          start: "top top",
          end: "+=280%",
          scrub: 0.6,
          pin: true,
        },
      });
      steps.forEach((step, i) => {
        pinTl.to(rail, { scaleX: (i + 1) / steps.length, duration: 0.5, ease: "none" }, i);
        pinTl.fromTo(
          step,
          { opacity: 0.18, y: 20 },
          { opacity: 1, y: 0, duration: 0.5, ease: "power2.out" },
          i
        );
        if (i > 0) pinTl.to(steps[i - 1], { opacity: 0.18, duration: 0.5 }, i);
      });

      // ---- Mandate REFUSED beat ----
      ScrollTrigger.create({
        trigger: q(".refuse"),
        start: "top 60%",
        once: true,
        onEnter: () => {
          const card = q(".refuse-card")[0] as HTMLElement;
          const flash = q(".refuse-flash")[0] as HTMLElement;
          gsap.timeline()
            .fromTo(flash, { opacity: 0 }, { opacity: 1, duration: 0.08 })
            .to(flash, { opacity: 0, duration: 0.5 }, "+=0.05")
            .fromTo(
              card,
              { x: -8 },
              { x: 0, duration: 0.5, ease: "elastic.out(1, 0.35)" },
              0
            )
            .fromTo(q(".stamp"), { opacity: 0, scale: 1.4, rotate: -14 }, { opacity: 1, scale: 1, rotate: -8, duration: 0.4, ease: "back.out(2)" }, 0.1);
        },
      });

      // ---- CTA reveal ----
      gsap.from(q(".cta-final > *"), {
        opacity: 0,
        y: 20,
        duration: 0.7,
        stagger: 0.1,
        ease: "power2.out",
        scrollTrigger: { trigger: q(".cta-final"), start: "top 80%" },
      });
    }, root);

    // refresh once fonts/images settle
    const onLoad = () => ScrollTrigger.refresh();
    window.addEventListener("load", onLoad);

    return () => {
      window.removeEventListener("load", onLoad);
      gsap.ticker.remove(ticker);
      lenis.destroy();
      ctx.revert();
    };
  }, []);

  return (
    <div ref={root} className="bg-bg text-ink overflow-clip">
      {/* fixed chrome */}
      <div className="fixed top-0 inset-x-0 z-50 pointer-events-none">
        <div className="max-w-[1160px] mx-auto px-6 h-16 flex items-center justify-between">
          <span className="font-mono font-medium tracking-tight text-ink pointer-events-auto">
            laeria<span className="text-accent">.</span>
          </span>
          <Link
            href="/login"
            className="pointer-events-auto font-mono text-[12px] tracking-wide uppercase text-ink-muted hover:text-ink transition-colors"
          >
            Sign in →
          </Link>
        </div>
      </div>

      {/* ================= HERO ================= */}
      <section className="hero relative min-h-screen flex items-center">
        {/* generated instrument plate, bleeding off the right */}
        <div className="hero-plate absolute right-0 top-1/2 -translate-y-1/2 w-[62%] max-w-[880px] aspect-[16/9] pointer-events-none">
          <div
            className="absolute inset-0 bg-cover bg-center"
            style={{ backgroundImage: "url(/hero-rail.png)" }}
          />
          {/* jade light sweep */}
          <div className="absolute inset-0 overflow-hidden mix-blend-screen">
            <div
              className="sweep absolute top-0 -left-1/3 h-full w-1/3"
              style={{
                background:
                  "linear-gradient(105deg, transparent, rgba(11,122,91,0.55), rgba(210,255,238,0.75), rgba(11,122,91,0.55), transparent)",
              }}
            />
          </div>
          {/* paper feather so the image melts into the page */}
          <div
            className="absolute inset-0"
            style={{
              background:
                "linear-gradient(90deg, var(--color-bg) 0%, transparent 34%, transparent 78%, rgba(250,250,248,0.5) 100%)",
            }}
          />
          <div
            className="absolute inset-0"
            style={{
              background:
                "linear-gradient(0deg, var(--color-bg), transparent 22%, transparent 80%, var(--color-bg))",
            }}
          />
        </div>

        <div className="hero-copy relative z-10 max-w-[1160px] mx-auto px-6 w-full">
          <div className="max-w-[640px]">
            <div className="hero-eyebrow eyebrow mb-5">
              Agentic payments · bounded by a mandate
            </div>
            <h1 className="text-[clamp(2.6rem,6vw,4.6rem)] leading-[1.02] font-semibold tracking-[-0.02em]">
              {HEAD_WORDS.map((w, i) => (
                <span key={i} className="hero-word inline-block mr-[0.24em]">
                  {w === "mandate." ? <span className="text-accent">{w}</span> : w}
                </span>
              ))}
            </h1>
            <p className="hero-sub mt-6 text-lg text-ink-muted max-w-[30rem]">
              laeria funds an account, finds the item, mints a single-use card
              capped to the purchase, and checks out — re-checking your spending
              rules at every step.
            </p>
            <div className="hero-cta mt-8 flex items-center gap-3">
              <Link
                href="/commerce"
                className="inline-flex items-center gap-2 bg-accent text-accent-ink font-medium text-sm px-5 py-2.5 rounded-[--radius] hover:bg-accent-hover transition-colors shadow-xs"
              >
                Enter the console
                <span aria-hidden>→</span>
              </Link>
              <Link
                href="/login"
                className="text-sm text-ink-muted hover:text-ink transition-colors px-3 py-2.5"
              >
                Sign in
              </Link>
            </div>
          </div>
        </div>

        <div className="hero-cue absolute bottom-8 left-1/2 -translate-x-1/2 eyebrow flex flex-col items-center gap-2">
          <span>Scroll</span>
          <span className="w-px h-8 bg-hairline-strong" />
        </div>
      </section>

      {/* ================= THESIS ================= */}
      <section className="py-[22vh] max-w-[1160px] mx-auto px-6">
        <p className="reveal text-[clamp(1.6rem,3.4vw,2.6rem)] leading-[1.28] font-medium tracking-[-0.01em] max-w-[56rem]">
          Most agents are handed a wallet and hoped for the best. laeria is
          handed a <span className="text-accent">mandate</span> — a set of
          spending rules it can prove it obeyed. Three price checks, a
          network-level card limit, a credential that dies after one use.
        </p>
        <div className="reveal mt-10 flex flex-wrap gap-x-10 gap-y-4">
          {[
            ["3×", "price verifications per purchase"],
            ["1", "single-use card, then destroyed"],
            ["0", "standing credentials to steal"],
          ].map(([n, l]) => (
            <div key={l} className="flex items-baseline gap-3">
              <span className="tnum text-3xl font-semibold text-ink">{n}</span>
              <span className="text-sm text-ink-muted max-w-[12rem]">{l}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ================= RESEARCH (decide what to buy) ================= */}
      <section className="py-[16vh] max-w-[1160px] mx-auto px-6">
        <div className="reveal max-w-[46rem]">
          <div className="eyebrow mb-4">Before it spends — it decides</div>
          <h2 className="text-[clamp(1.7rem,3.8vw,2.8rem)] font-semibold tracking-[-0.02em] leading-[1.1] mb-4">
            The agent doesn&apos;t guess what to buy. It reads the room.
          </h2>
          <p className="text-ink-muted text-lg max-w-[38rem]">
            laeria mines real Reddit threads — human signal, not marketing — to
            decide <em>what</em> is worth buying, learn how it turned out for
            people who already bought it, and keep watching after you commit.
          </p>
        </div>
        <div className="reveal mt-10 grid grid-cols-1 md:grid-cols-3 gap-4">
          {[
            ["What to buy", "Honest community consensus on a purchase — the pick, the red flags, the alternatives.", "/decision"],
            ["How it went", "Update posts from people who made the same call and came back to report how it turned out.", "/research"],
            ["Monitor", "Watches what you own for a change in the signal — and can act within the mandate.", "/monitor"],
          ].map(([title, body, href]) => (
            <Link
              key={title}
              href={href}
              className="group bg-surface border border-hairline rounded-[--radius-lg] p-6 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all"
            >
              <div className="text-lg font-semibold text-ink mb-2 flex items-center justify-between">
                {title}
                <span className="text-accent opacity-0 group-hover:opacity-100 transition-opacity">→</span>
              </div>
              <p className="text-sm text-ink-muted leading-relaxed">{body}</p>
            </Link>
          ))}
        </div>
      </section>

      {/* ================= LIFECYCLE (pinned) ================= */}
      <section className="lifecycle relative min-h-screen flex items-center">
        <div className="max-w-[1160px] mx-auto px-6 w-full">
          <div className="eyebrow mb-3">The lifecycle</div>
          <div className="h-px w-full bg-hairline relative mb-12">
            <div className="rail-fill absolute inset-y-0 left-0 w-full bg-accent origin-left" style={{ transform: "scaleX(0)" }} />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
            {STEPS.map((s) => (
              <div key={s.n} className="step">
                <div className="tnum text-accent text-sm mb-3">{s.n}</div>
                <div className="text-2xl font-semibold mb-2">{s.k}</div>
                <div className="text-sm text-ink-muted leading-relaxed">{s.d}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ================= REFUSED beat ================= */}
      <section className="refuse relative py-[20vh] max-w-[1160px] mx-auto px-6">
        <div className="refuse-flash fixed inset-0 z-40 pointer-events-none bg-danger/25 opacity-0" />
        <div className="max-w-[40rem]">
          <div className="eyebrow mb-4">The differentiator</div>
          <h2 className="text-[clamp(1.8rem,4vw,3rem)] font-semibold tracking-[-0.02em] leading-[1.08] mb-8">
            When a purchase breaks the rules, the agent doesn&apos;t.
          </h2>
        </div>
        <div className="refuse-card relative max-w-[34rem] bg-surface border border-danger/30 rounded-[--radius-lg] p-6 shadow-md">
          <div className="stamp absolute -top-4 right-6 font-mono text-sm font-semibold tracking-wider uppercase text-danger border-2 border-danger rounded px-3 py-1 bg-danger-soft">
            Refused
          </div>
          <div className="flex items-center justify-between mb-4">
            <span className="eyebrow">purchase attempt</span>
            <span className="tnum text-sm text-ink-muted">$142.00</span>
          </div>
          <div className="font-mono text-sm text-ink-muted space-y-1.5">
            <div className="flex justify-between"><span>per-transaction cap</span><span className="tnum text-ink">$50.00</span></div>
            <div className="flex justify-between"><span>requested</span><span className="tnum text-danger">$142.00</span></div>
            <div className="h-px bg-hairline my-2" />
            <div className="flex justify-between text-danger"><span>mandate check</span><span>exceeds cap → refused</span></div>
          </div>
          <p className="text-sm text-ink-muted mt-4">
            No card is ever issued. The refusal, with its reason, lands in the
            audit trail — a demo of the agent saying <em>no</em>.
          </p>
        </div>
      </section>

      {/* ================= CTA ================= */}
      <section className="cta-final relative py-[24vh] max-w-[1160px] mx-auto px-6 text-center">
        <div className="eyebrow mb-4">Ready</div>
        <h2 className="text-[clamp(2.2rem,5vw,3.6rem)] font-semibold tracking-[-0.02em] mb-8">
          Watch it run the whole lifecycle.
        </h2>
        <div className="flex items-center justify-center gap-3">
          <Link
            href="/commerce"
            className="inline-flex items-center gap-2 bg-accent text-accent-ink font-medium px-6 py-3 rounded-[--radius] hover:bg-accent-hover transition-colors shadow-sm"
          >
            Enter the console <span aria-hidden>→</span>
          </Link>
          <Link
            href="/login"
            className="px-5 py-3 text-ink-muted hover:text-ink transition-colors"
          >
            Sign in
          </Link>
        </div>
        <div className="mt-16 pt-8 border-t border-hairline flex items-center justify-between text-ink-subtle">
          <span className="font-mono text-sm">laeria<span className="text-accent">.</span></span>
          <span className="font-mono text-[11px] tracking-wide uppercase">
            Agentic payment lifecycle · demo
          </span>
        </div>
      </section>
    </div>
  );
}
