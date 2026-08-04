"use client";

// laeria — landing. Reddit decision research is the lead story; the payment
// lifecycle is the payoff, not the pitch. The page reads as a thread being
// mined down to a verdict, then acted on under a mandate.

import { useEffect, useRef } from "react";
import Link from "next/link";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";

const HEAD_WORDS = ["The", "real", "review", "isn't", "a", "review.", "It's", "a", "thread."];

// Ambient hero stream. Illustrative of what the agent reads — no usernames,
// no real quotes, no brands.
const STREAM_A = [
  { sub: "MechanicalKeyboards", age: "3y", score: "2.4k", t: "3 years on my sub-$100 board — the honest update" },
  { sub: "BuyItForLife", age: "5y", score: "912", t: "Which cheap boards actually survive daily use?" },
  { sub: "ErgoMechKeyboards", age: "11mo", score: "340", t: "Wrist pain after switching — what I got wrong" },
  { sub: "battlestations", age: "2y", score: "1.1k", t: "Regret thread: the upgrade I shouldn't have paid for" },
  { sub: "AskElectronics", age: "7y", score: "504", t: "Why the cheap ones die at the cable, every time" },
  { sub: "MechanicalKeyboards", age: "4mo", score: "188", t: "Six months in and the coating is wearing off" },
];
const STREAM_B = [
  { sub: "buildapc", age: "4y", score: "3.8k", t: "Stop buying the board every listicle recommends" },
  { sub: "MechanicalKeyboards", age: "8mo", score: "656", t: "Stabilizers came dry again. Am I the only one?" },
  { sub: "programming", age: "6y", score: "1.9k", t: "What do you actually type on 8 hours a day?" },
  { sub: "frugalmalefashion", age: "1y", score: "428", t: "Under $100 and I'd buy it again — 14 months in" },
  { sub: "sysadmin", age: "2y", score: "1.3k", t: "The one I've replaced three times, and the one I haven't" },
  { sub: "BuyItForLife", age: "9mo", score: "771", t: "Update: warranty claim two years later. How it went." },
];

// The mining scene: raw signal on the left, resolved verdict on the right.
const MINED = [
  {
    sub: "MechanicalKeyboards",
    age: "3y",
    score: "2.4k",
    t: "3 years on my sub-$100 board — the honest update",
    q: "Still typing on it daily. The switches are fine. The stabilizers were the problem and nobody mentions that in reviews.",
    pull: "stabilizers ship dry",
  },
  {
    sub: "BuyItForLife",
    age: "5y",
    score: "912",
    t: "Which cheap boards actually survive daily use?",
    q: "Hot-swap is the only spec that matters at this price. Everything else you can fix later. A soldered board is a board you throw away.",
    pull: "hot-swap or skip it",
  },
  {
    sub: "buildapc",
    age: "4y",
    score: "3.8k",
    t: "Stop buying the board every listicle recommends",
    q: "Half those roundups are affiliate. The one that keeps getting recommended by people who own it isn't the one on page one.",
    pull: "page one is affiliate",
  },
  {
    sub: "MechanicalKeyboards",
    age: "8mo",
    score: "656",
    t: "The 'silent' switches are not silent",
    q: "Bought them for an open office on the strength of the name. They are quieter. They are not quiet. Ask before you commit.",
    pull: "\"silent\" ≠ quiet",
  },
];

const MODES = [
  {
    href: "/decision",
    label: "Worth it?",
    line: "Ask before you commit.",
    body: "A purchase, a procedure, a job, a move. The agent identifies the communities that would actually know, reads the threads, and returns the consensus with the red flags and the alternatives attached.",
    meta: "reads threads → verdict",
  },
  {
    href: "/research",
    label: "How it went",
    line: "Find the people who came back.",
    body: "Follow-up posts from people six months and three years on — the update threads where the honeymoon wore off and the real failure modes showed up.",
    meta: "mines update posts",
  },
  {
    href: "/monitor",
    label: "Monitor",
    line: "Keep watching after you commit.",
    body: "The signal on a product moves. A revision gets worse, a recall lands, a better option appears. The agent keeps reading and tells you when the answer changed.",
    meta: "watches the signal",
  },
];

const STEPS = [
  { n: "01", k: "Fund", d: "Stablecoins move into a KYC-ready account. A balance, not a blank cheque." },
  { n: "02", k: "Discover", d: "It opens the real storefront in a browser and re-reads the price a human would be charged." },
  { n: "03", k: "Issue", d: "A single-use virtual card, its limit pinned to this one purchase." },
  { n: "04", k: "Execute", d: "Checkout completes, the card dies, every number lands in the audit trail." },
];

function Caret({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 12 10" aria-hidden className={`w-2.5 h-2 ${className}`} fill="currentColor">
      <path d="M6 0 12 8.5H0z" />
    </svg>
  );
}

function ThreadCard({
  sub,
  age,
  score,
  t,
  className = "",
}: {
  sub: string;
  age: string;
  score: string;
  t: string;
  className?: string;
}) {
  return (
    <div
      className={`bg-surface border border-hairline rounded-[--radius] p-4 shadow-xs flex gap-3 ${className}`}
    >
      <div className="flex flex-col items-center gap-1 pt-0.5 shrink-0">
        <Caret className="text-signal" />
        <span className="tnum text-[11px] text-ink-muted leading-none">{score}</span>
      </div>
      <div className="min-w-0">
        <div className="font-mono text-[10px] tracking-wide text-ink-subtle mb-1.5 truncate">
          r/{sub} · {age}
        </div>
        <div className="text-[13px] leading-snug text-ink">{t}</div>
      </div>
    </div>
  );
}

export default function Landing() {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;

    gsap.registerPlugin(ScrollTrigger);
    const lenis = new Lenis({ lerp: 0.1, wheelMultiplier: 1 });
    lenis.on("scroll", ScrollTrigger.update);
    const ticker = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(ticker);
    gsap.ticker.lagSmoothing(0);

    const ctx = gsap.context((self) => {
      const q = self.selector!;

      // ---- Hero entrance ----
      gsap
        .timeline({ defaults: { ease: "power3.out" } })
        .from(q(".hero-eyebrow"), { opacity: 0, y: 12, duration: 0.6 })
        .from(q(".hero-word"), { opacity: 0, y: 26, duration: 0.7, stagger: 0.05 }, "-=0.2")
        .from(q(".hero-sub"), { opacity: 0, y: 14, duration: 0.6 }, "-=0.3")
        .from(q(".hero-cta"), { opacity: 0, y: 10, duration: 0.5 }, "-=0.3")
        .from(q(".hero-stream"), { opacity: 0, duration: 1.2, ease: "power2.out" }, 0.15)
        .from(q(".hero-cue"), { opacity: 0, duration: 0.6 }, "-=0.2");

      // thread columns drift forever — the feed the agent is reading
      gsap.utils.toArray<HTMLElement>(q(".drift")).forEach((col, i) => {
        gsap.to(col, {
          yPercent: -50,
          duration: i === 0 ? 34 : 42,
          ease: "none",
          repeat: -1,
        });
      });

      // hero parallax
      gsap.to(q(".hero-stream"), {
        yPercent: 10,
        ease: "none",
        scrollTrigger: { trigger: q(".hero"), start: "top top", end: "bottom top", scrub: true },
      });
      gsap.to(q(".hero-copy"), {
        yPercent: -16,
        opacity: 0.12,
        ease: "none",
        scrollTrigger: { trigger: q(".hero"), start: "top top", end: "bottom top", scrub: true },
      });

      // chrome gets a paper backdrop once the hero is behind us
      gsap.to(q(".chrome-bg"), {
        opacity: 1,
        duration: 0.3,
        ease: "none",
        scrollTrigger: { trigger: q(".hero"), start: "bottom 90%", toggleActions: "play none none reverse" },
      });

      // ---- generic reveals ----
      gsap.utils.toArray<HTMLElement>(q(".reveal")).forEach((el) => {
        gsap.from(el, {
          opacity: 0,
          y: 24,
          duration: 0.8,
          ease: "power2.out",
          scrollTrigger: { trigger: el, start: "top 82%" },
        });
      });

      // ---- Mining scene: pinned, threads resolve into a verdict ----
      // pinning only makes sense where the scene fits the viewport; below lg
      // the section stacks and simply scrubs as it passes.
      const pinnable = window.matchMedia("(min-width: 1024px)").matches;
      const rows = gsap.utils.toArray<HTMLElement>(q(".mine-row"));
      const mineTl = gsap.timeline({
        scrollTrigger: {
          trigger: q(".mine"),
          start: pinnable ? "top top" : "top 80%",
          end: pinnable ? "+=260%" : "bottom 60%",
          scrub: 0.6,
          pin: pinnable,
        },
      });

      rows.forEach((row, i) => {
        const rule = row.querySelector(".mine-rule");
        const pull = row.querySelector(".mine-pull");
        mineTl
          .to(row, { opacity: 1, duration: 0.4, ease: "power2.out" }, i * 0.8)
          .fromTo(rule, { scaleY: 0 }, { scaleY: 1, duration: 0.4, ease: "power2.out" }, i * 0.8)
          .fromTo(
            pull,
            { opacity: 0, x: -6 },
            { opacity: 1, x: 0, duration: 0.35, ease: "power2.out" },
            i * 0.8 + 0.15
          );
      });

      // counters tick while the threads are being read
      const counters = gsap.utils.toArray<HTMLElement>(q(".mine-count"));
      counters.forEach((el) => {
        const to = Number(el.dataset.to || 0);
        const obj = { v: 0 };
        mineTl.to(
          obj,
          {
            v: to,
            duration: rows.length * 0.8,
            ease: "none",
            onUpdate: () => {
              el.textContent = Math.round(obj.v).toLocaleString();
            },
          },
          0
        );
      });

      // the panel resolves in one move — skeleton out, verdict in — so there
      // is never a scroll position where the card reads as empty
      const resolve = rows.length * 0.8;
      mineTl
        .to(q(".mine-skeleton"), { opacity: 0, duration: 0.3 }, resolve - 0.3)
        .fromTo(
          q(".verdict"),
          { opacity: 0, y: 18 },
          { opacity: 1, y: 0, duration: 0.5, ease: "power2.out" },
          resolve - 0.1
        )
        // hold the resolved state for the tail of the pin
        .to({}, { duration: 0.6 });

      // ---- Payment section: rail fill + refusal stamp ----
      gsap.fromTo(
        q(".rail-fill"),
        { scaleX: 0 },
        {
          scaleX: 1,
          ease: "none",
          scrollTrigger: { trigger: q(".lifecycle"), start: "top 70%", end: "bottom 70%", scrub: 0.5 },
        }
      );
      ScrollTrigger.create({
        trigger: q(".refuse-card"),
        start: "top 78%",
        once: true,
        onEnter: () => {
          gsap
            .timeline()
            .fromTo(q(".refuse-card"), { x: -8 }, { x: 0, duration: 0.5, ease: "elastic.out(1, 0.35)" })
            .fromTo(
              q(".stamp"),
              { opacity: 0, scale: 1.4, rotate: -14 },
              { opacity: 1, scale: 1, rotate: -8, duration: 0.4, ease: "back.out(2)" },
              0.1
            );
        },
      });

      // ---- CTA ----
      gsap.from(q(".cta-final > *"), {
        opacity: 0,
        y: 20,
        duration: 0.7,
        stagger: 0.1,
        ease: "power2.out",
        scrollTrigger: { trigger: q(".cta-final"), start: "top 80%" },
      });
    }, root);

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
        {/* paper backdrop fades in past the hero so the wordmark never sits
            on top of section copy */}
        <div className="chrome-bg absolute inset-0 bg-bg/85 border-b border-hairline supports-[backdrop-filter]:bg-bg/70 supports-[backdrop-filter]:backdrop-blur-sm opacity-0" />
        <div className="relative max-w-[1160px] mx-auto px-6 h-16 flex items-center justify-between">
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
        {/* the feed, drifting behind the copy */}
        <div className="hero-stream absolute right-0 top-0 h-full w-[58%] max-w-[760px] pointer-events-none hidden md:block">
          {/* two nested masks — vertical then horizontal. mask-composite is
              unreliable across engines, so each layer carries exactly one. */}
          <div
            className="absolute inset-0 overflow-hidden"
            style={{
              maskImage: "linear-gradient(180deg, transparent, #000 32%, #000 66%, transparent)",
              WebkitMaskImage:
                "linear-gradient(180deg, transparent, #000 32%, #000 66%, transparent)",
            }}
          >
            <div
              className="absolute inset-0"
              style={{
                maskImage: "linear-gradient(90deg, transparent, #000 44%)",
                WebkitMaskImage: "linear-gradient(90deg, transparent, #000 44%)",
              }}
            >
              <div className="absolute inset-0 grid grid-cols-2 gap-4 px-6 opacity-[0.85]">
                {[STREAM_A, STREAM_B].map((col, ci) => (
                  <div key={ci} className={ci === 1 ? "pt-16" : ""}>
                    <div className="drift grid gap-4">
                      {[...col, ...col].map((th, i) => (
                        <ThreadCard key={i} {...th} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="hero-copy relative z-10 max-w-[1160px] mx-auto px-6 w-full">
          <div className="max-w-[640px]">
            <div className="hero-eyebrow eyebrow mb-5">
              Decision research · human signal, not marketing
            </div>
            <h1 className="text-[clamp(2.6rem,6vw,4.6rem)] leading-[1.02] font-semibold tracking-[-0.02em]">
              {HEAD_WORDS.map((w, i) => (
                <span key={i} className="hero-word inline-block mr-[0.24em]">
                  {w === "thread." ? <span className="text-accent">{w}</span> : w}
                </span>
              ))}
            </h1>
            <p className="hero-sub mt-6 text-lg text-ink-muted max-w-[32rem]">
              laeria reads the communities that actually own the thing — hundreds
              of comments, years of follow-ups — and returns the consensus you
              would reach after a week of lurking. Then, if you want it to, it
              buys the pick under a spending mandate.
            </p>
            <div className="hero-cta mt-8 flex items-center gap-3">
              <Link
                href="/decision"
                className="inline-flex items-center gap-2 whitespace-nowrap bg-accent text-accent-ink font-medium text-sm px-5 py-2.5 rounded-[--radius] hover:bg-accent-hover transition-colors shadow-xs"
              >
                Ask what's worth it
                <span aria-hidden>→</span>
              </Link>
              <Link
                href="/research"
                className="text-sm text-ink-muted hover:text-ink transition-colors px-3 py-2.5"
              >
                See how it went for other buyers
              </Link>
            </div>
          </div>
        </div>

        <div className="hero-cue absolute bottom-8 left-1/2 -translate-x-1/2 eyebrow flex flex-col items-center gap-2">
          <span>Scroll</span>
          <span className="w-px h-8 bg-hairline-strong" />
        </div>
      </section>

      {/* ================= THE PROBLEM ================= */}
      <section className="py-[18vh] max-w-[1160px] mx-auto px-6">
        <div className="reveal max-w-[46rem]">
          <div className="eyebrow mb-4">The problem</div>
          <h2 className="text-[clamp(1.7rem,3.8vw,2.8rem)] font-semibold tracking-[-0.02em] leading-[1.1]">
            Page one is ranked by SEO budget. The truth is on page four of a
            thread, posted by someone who owned it for{" "}
            <span className="text-accent">three years</span>.
          </h2>
        </div>

        <div className="reveal mt-12 grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
          {/* what ranks */}
          <div className="bg-surface-2 border border-hairline rounded-[--radius-lg] p-6">
            <div className="eyebrow mb-4">What ranks</div>
            <ul className="grid gap-3">
              {[
                "10 Best Keyboards of 2026 (#3 Will Surprise You)",
                "Top Picks — Updated Weekly",
                "The Ultimate Buyer's Guide",
              ].map((s) => (
                <li
                  key={s}
                  className="text-sm text-ink-subtle line-through decoration-hairline-strong"
                >
                  {s}
                </li>
              ))}
            </ul>
            <p className="mt-5 text-sm text-ink-muted leading-relaxed">
              Written by people who never bought it, ranked by whoever spent the
              most on distribution, monetised by the link you click.
            </p>
          </div>

          {/* what's true */}
          <div className="bg-surface border border-hairline rounded-[--radius-lg] p-6 shadow-sm relative">
            <div className="eyebrow mb-4 text-accent">What&apos;s true</div>
            <div className="grid gap-3">
              {STREAM_A.slice(0, 3).map((th) => (
                <ThreadCard key={th.t} {...th} className="!shadow-none" />
              ))}
            </div>
            <p className="mt-5 text-sm text-ink-muted leading-relaxed">
              Written by people who paid for it, ranked by other owners, and
              updated when it broke. Nobody is paid to post the regret thread.
            </p>
          </div>
        </div>
      </section>

      {/* ================= MINING SCENE (pinned) ================= */}
      <section className="mine relative lg:h-screen flex items-center overflow-hidden py-24 lg:py-0">
        <div className="max-w-[1160px] mx-auto px-6 w-full">
          <div className="mb-8">
            <div className="eyebrow mb-3">How it decides</div>
            <h2 className="text-[clamp(1.4rem,2.8vw,2rem)] font-semibold tracking-[-0.02em] leading-[1.12] max-w-[34rem]">
              It reads the thread the way you would — if you had a week.
            </h2>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-[1.05fr_0.95fr] gap-10 items-start">
            {/* raw signal */}
            <div className="grid gap-3">
              {MINED.map((m) => (
                <div key={m.t} className="mine-row opacity-25 flex gap-4">
                  <div className="relative w-px bg-hairline shrink-0">
                    <div className="mine-rule absolute inset-0 bg-accent origin-top" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-3">
                      <span className="flex items-center gap-1 text-signal">
                        <Caret />
                        <span className="tnum text-[11px] leading-none">{m.score}</span>
                      </span>
                      <span className="font-mono text-[10px] tracking-wide text-ink-subtle truncate">
                        r/{m.sub} · {m.age}
                      </span>
                    </div>
                    <div className="text-sm font-medium text-ink mt-1">{m.t}</div>
                    <p className="text-[13px] leading-snug text-ink-muted mt-1 line-clamp-2">
                      {m.q}
                    </p>
                    <div className="mine-pull mt-1.5 inline-flex items-center gap-2 bg-accent-soft text-accent font-mono text-[11px] px-2 py-0.5 rounded-[--radius-sm]">
                      <span aria-hidden>→</span>
                      {m.pull}
                    </div>
                  </div>
                </div>
              ))}
              <p className="font-mono text-[10px] tracking-wide uppercase text-ink-subtle mt-1">
                Illustrative — threads shown are composites, not quotations
              </p>
            </div>

            {/* the panel is present the whole time: it counts while reading,
                then resolves into the same verdict shape the app returns */}
            <div className="bg-surface border border-hairline rounded-[--radius-lg] shadow-md p-6 relative">
              <div className="flex items-center justify-between pb-4 border-b border-hairline">
                <div className="eyebrow">signal</div>
                <div className="flex gap-6">
                  {[
                    ["38", "threads"],
                    ["2410", "comments"],
                    ["6", "communities"],
                  ].map(([to, label]) => (
                    <div key={label} className="text-right">
                      <div className="tnum text-lg font-semibold text-ink leading-none">
                        <span className="mine-count" data-to={to}>
                          0
                        </span>
                      </div>
                      <div className="eyebrow mt-1">{label}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* skeleton and verdict share one grid cell, so the card is
                  sized by the taller of the two and never jumps or clips */}
              <div className="pt-5 grid">
                <div className="mine-skeleton [grid-area:1/1] grid gap-2.5 content-start">
                  {["92%", "78%", "86%", "54%", "70%", "40%"].map((w, i) => (
                    <div
                      key={i}
                      className={`h-3 rounded-full ${i === 3 ? "mt-3" : ""} bg-surface-2`}
                      style={{ width: w }}
                    />
                  ))}
                </div>

                <div className="verdict [grid-area:1/1] opacity-0">
                <div className="verdict-line flex items-center justify-between mb-3">
                  <h3 className="text-lg font-semibold">Verdict</h3>
                  <span className="font-mono text-[11px] uppercase tracking-wide bg-accent-soft text-accent px-2 py-1 rounded-[--radius-sm]">
                    high confidence
                  </span>
                </div>
                <p className="verdict-line text-[15px] leading-relaxed text-ink">
                  A hot-swappable 65% with PBT caps — and budget an evening to
                  lube the stabilizers, the complaint that outlives every other
                  one.
                </p>
                <p className="verdict-line text-[12px] text-ink-subtle mt-2">
                  Based on <span className="tnum">38</span> threads across
                  r/MechanicalKeyboards, r/BuyItForLife, r/buildapc · 2021–2026
                </p>
                <div className="verdict-line mt-4 pt-4 border-t border-hairline grid gap-1.5">
                  <div className="eyebrow mb-1">Red flags the reviews skipped</div>
                  {[
                    "Stabilizers ship dry — rattle out of the box",
                    "“Silent” switches are quieter, not quiet",
                    "Soldered boards at this price are disposable",
                  ].map((r) => (
                    <div key={r} className="flex gap-2.5 text-[13px] text-ink-muted leading-snug">
                      <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-danger shrink-0" />
                      {r}
                    </div>
                  ))}
                </div>
                <Link
                  href="/decision"
                  className="verdict-line mt-4 inline-flex items-center gap-2 text-sm font-medium text-accent hover:text-accent-hover transition-colors"
                >
                  Run this on your own purchase <span aria-hidden>→</span>
                </Link>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ================= THREE QUESTIONS ================= */}
      <section className="py-[16vh] max-w-[1160px] mx-auto px-6">
        <div className="reveal max-w-[44rem] mb-10">
          <div className="eyebrow mb-4">Three questions</div>
          <h2 className="text-[clamp(1.7rem,3.8vw,2.6rem)] font-semibold tracking-[-0.02em] leading-[1.1]">
            Before you commit, after you commit, and every month it's still yours.
          </h2>
        </div>
        <div className="reveal grid grid-cols-1 md:grid-cols-3 gap-4">
          {MODES.map((m) => (
            <Link
              key={m.href}
              href={m.href}
              className="group bg-surface border border-hairline rounded-[--radius-lg] p-6 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all flex flex-col"
            >
              <div className="flex items-center justify-between mb-4">
                <span className="font-mono text-[10px] tracking-wide uppercase text-ink-subtle">
                  {m.meta}
                </span>
                <span className="text-accent opacity-0 group-hover:opacity-100 transition-opacity">
                  →
                </span>
              </div>
              <div className="text-lg font-semibold text-ink">{m.label}</div>
              <div className="text-sm text-accent mb-3">{m.line}</div>
              <p className="text-sm text-ink-muted leading-relaxed">{m.body}</p>
            </Link>
          ))}
        </div>
      </section>

      {/* ================= THEN IT CAN ACT (payments, subordinate) ================= */}
      <section className="lifecycle relative py-[16vh] overflow-clip">
        {/* the instrument plate, now the payoff rather than the pitch */}
        <div className="absolute right-0 bottom-0 w-[46%] max-w-[620px] aspect-[16/9] pointer-events-none hidden lg:block opacity-[0.38]">
          <div
            className="absolute inset-0 bg-cover bg-center"
            style={{ backgroundImage: "url(/hero-rail.png)" }}
          />
          <div
            className="absolute inset-0"
            style={{
              background:
                "linear-gradient(90deg, var(--color-bg) 0%, rgba(250,250,248,0.55) 55%, transparent 85%), linear-gradient(0deg, var(--color-bg) 0%, transparent 40%)",
            }}
          />
        </div>

        <div className="relative max-w-[1160px] mx-auto px-6">
          <div className="reveal max-w-[46rem]">
            <div className="eyebrow mb-4">And then it acts</div>
            <h2 className="text-[clamp(1.7rem,3.8vw,2.8rem)] font-semibold tracking-[-0.02em] leading-[1.1] mb-4">
              Research is worth less if you still have to go buy it yourself.
            </h2>
            <p className="text-ink-muted text-lg max-w-[38rem]">
              When the consensus is strong, laeria can complete the purchase —
              funding a real balance, finding the item on a live storefront, and
              minting a single-use card capped to that one price. Every step is
              re-checked against a spending{" "}
              <span className="text-accent">mandate</span> it can prove it obeyed.
            </p>
          </div>

          <div className="reveal mt-12">
            <div className="h-px w-full bg-hairline relative mb-10">
              <div
                className="rail-fill absolute inset-y-0 left-0 w-full bg-accent origin-left"
                style={{ transform: "scaleX(0)" }}
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
              {STEPS.map((s) => (
                <div key={s.n}>
                  <div className="tnum text-accent text-sm mb-3">{s.n}</div>
                  <div className="text-xl font-semibold mb-2">{s.k}</div>
                  <div className="text-sm text-ink-muted leading-relaxed">{s.d}</div>
                </div>
              ))}
            </div>
          </div>

          {/* the refusal — the proof the mandate is real */}
          <div className="reveal mt-16 grid grid-cols-1 lg:grid-cols-[0.9fr_1.1fr] gap-8 items-center">
            <div className="refuse-card relative bg-surface border border-danger/30 rounded-[--radius-lg] p-6 shadow-md max-w-[34rem]">
              <div className="stamp absolute -top-4 right-6 font-mono text-sm font-semibold tracking-wider uppercase text-danger border-2 border-danger rounded px-3 py-1 bg-danger-soft">
                Refused
              </div>
              <div className="flex items-center justify-between mb-4">
                <span className="eyebrow">purchase attempt</span>
                <span className="tnum text-sm text-ink-muted">$142.00</span>
              </div>
              <div className="font-mono text-sm text-ink-muted space-y-1.5">
                <div className="flex justify-between">
                  <span>per-transaction cap</span>
                  <span className="tnum text-ink">$50.00</span>
                </div>
                <div className="flex justify-between">
                  <span>requested</span>
                  <span className="tnum text-danger">$142.00</span>
                </div>
                <div className="h-px bg-hairline my-2" />
                <div className="flex justify-between text-danger">
                  <span>mandate check</span>
                  <span>exceeds cap → refused</span>
                </div>
              </div>
            </div>
            <div className="max-w-[32rem]">
              <h3 className="text-xl font-semibold tracking-[-0.01em] mb-3">
                When a purchase breaks the rules, the agent doesn&apos;t.
              </h3>
              <p className="text-ink-muted leading-relaxed">
                No card is ever issued. The refusal and its reason land in the
                audit trail beside the successful buys. Three price checks, a
                network-level card limit, and zero standing credentials to steal.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ================= CTA ================= */}
      <section className="cta-final relative pt-[10vh] pb-[16vh] max-w-[1160px] mx-auto px-6 text-center">
        <div className="eyebrow mb-4">Start with a question</div>
        <h2 className="text-[clamp(2.2rem,5vw,3.6rem)] font-semibold tracking-[-0.02em] mb-8">
          What are you about to commit to?
        </h2>
        <div className="flex items-center justify-center gap-3">
          <Link
            href="/decision"
            className="inline-flex items-center gap-2 bg-accent text-accent-ink font-medium px-6 py-3 rounded-[--radius] hover:bg-accent-hover transition-colors shadow-sm"
          >
            Ask what's worth it <span aria-hidden>→</span>
          </Link>
          <Link href="/login" className="px-5 py-3 text-ink-muted hover:text-ink transition-colors">
            Sign in
          </Link>
        </div>
        <div className="mt-16 pt-8 border-t border-hairline flex items-center justify-between text-ink-subtle">
          <span className="font-mono text-sm">
            laeria<span className="text-accent">.</span>
          </span>
          <span className="font-mono text-[11px] tracking-wide uppercase">
            Reddit decision research · agentic checkout · demo
          </span>
        </div>
      </section>
    </div>
  );
}
