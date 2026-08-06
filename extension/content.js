// Runs in the page. Two jobs, both thin:
//
//   1. Work out whether this page is a product page or an order confirmation.
//   2. Render a verdict, and hand every decision that needs a token or an LLM
//      to the service worker.
//
// No research logic, no mandate logic, no credentials. If this file ever needs
// a token, something has been put in the wrong place.

(() => {
  "use strict";

  // Don't annotate laeria's own surfaces.
  if (/(^|\.)laeria-ai\.vercel\.app$|^localhost$/.test(location.hostname)) return;
  if (window.top !== window) return;

  // ---- page understanding ----------------------------------------------

  /** Every JSON-LD node on the page, flattened through arrays and @graph. */
  function jsonLdNodes() {
    const out = [];
    const walk = (node) => {
      if (Array.isArray(node)) return node.forEach(walk);
      if (!node || typeof node !== "object") return;
      out.push(node);
      if (node["@graph"]) walk(node["@graph"]);
    };
    for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        walk(JSON.parse(el.textContent));
      } catch {
        /* malformed blocks are common in the wild; skip quietly */
      }
    }
    return out;
  }

  const typesOf = (node) => {
    const t = node["@type"];
    return (Array.isArray(t) ? t : [t]).filter(Boolean).map(String);
  };

  const meta = (sel) => document.querySelector(sel)?.getAttribute("content") || "";

  function parsePrice(value) {
    if (value == null) return 0;
    const n = parseFloat(String(value).replace(/[^0-9.]/g, ""));
    return Number.isFinite(n) ? n : 0;
  }

  /**
   * Structured data first, OpenGraph second, URL shape last. The order is
   * deliberate: JSON-LD is what the merchant asserts a page IS, whereas a
   * /products/ path is only what it looks like, and category and search pages
   * hit that pattern too.
   */
  function detectProduct() {
    for (const node of jsonLdNodes()) {
      // ProductGroup is what Shopify emits for anything with variants, which
      // is most of a real catalogue — matching only "Product" silently drops
      // to the OpenGraph fallback on those pages and loses the currency.
      const types = typesOf(node);
      if (!types.includes("Product") && !types.includes("ProductGroup")) continue;
      const title = String(node.name || "").trim();
      if (!title) continue;
      // A ProductGroup carries its price on the variants rather than itself.
      const variant = Array.isArray(node.hasVariant) ? node.hasVariant[0] : node.hasVariant;
      const rawOffers = node.offers ?? variant?.offers;
      const offers = Array.isArray(rawOffers) ? rawOffers[0] : rawOffers;
      return {
        title,
        price: parsePrice(offers?.price ?? offers?.lowPrice),
        currency: offers?.priceCurrency || "",
        image: typeof node.image === "string" ? node.image : node.image?.[0] || "",
        source: "json-ld",
      };
    }

    const ogType = meta('meta[property="og:type"]');
    const ogPrice = meta('meta[property="product:price:amount"]') ||
                    meta('meta[property="og:price:amount"]');
    if (ogType === "product" || ogPrice) {
      const title = meta('meta[property="og:title"]') || document.title;
      if (title.trim()) {
        return {
          title: title.trim(),
          price: parsePrice(ogPrice),
          currency: meta('meta[property="product:price:currency"]') || "",
          image: meta('meta[property="og:image"]'),
          source: "opengraph",
        };
      }
    }

    // Shopify and a few common shapes, only when nothing above fired.
    if (/\/products\/[^/]+$/.test(location.pathname)) {
      const title = meta('meta[property="og:title"]') ||
                    document.querySelector("h1")?.textContent?.trim() || "";
      if (title) {
        return { title, price: 0, currency: "", image: meta('meta[property="og:image"]'),
                 source: "url-shape" };
      }
    }
    if (/^www\.amazon\./.test(location.hostname)) {
      const title = document.querySelector("#productTitle")?.textContent?.trim();
      if (title) return { title, price: 0, currency: "", image: "", source: "amazon" };
    }
    return null;
  }

  /**
   * Order confirmations. Shopify's thank-you page is the one that matters for
   * the demo — it is where the agent's own purchase lands — but a JSON-LD
   * Order is honoured anywhere it appears.
   */
  function detectOrder() {
    for (const node of jsonLdNodes()) {
      if (!typesOf(node).includes("Order")) continue;
      const raw = node.orderedItem || [];
      const items = (Array.isArray(raw) ? raw : [raw])
        .map((entry) => {
          const p = entry?.orderedItem || entry;
          return { name: String(p?.name || "").trim(), category: p?.category || "" };
        })
        .filter((i) => i.name.length > 1);
      if (items.length) return { items, source: "json-ld" };
    }

    const isShopifyThankYou =
      /\/thank[_-]?you\b/.test(location.pathname) ||
      /\/orders\/[a-z0-9]{16,}/i.test(location.pathname) ||
      document.querySelector('[data-step="thank_you"]') !== null;

    if (isShopifyThankYou) {
      const nodes = document.querySelectorAll(
        ".product__description__name, [data-product-title], .product-thumbnail__label"
      );
      const seen = new Set();
      const items = [];
      for (const el of nodes) {
        const name = el.textContent.replace(/\s+/g, " ").trim();
        if (name.length > 1 && !seen.has(name)) {
          seen.add(name);
          items.push({ name, category: "" });
        }
      }
      if (items.length) return { items, source: "shopify" };
    }
    return null;
  }

  // ---- overlay ----------------------------------------------------------
  //
  // Shadow DOM, because this renders on shops we do not control and their CSS
  // is hostile by default. Nothing here inherits from or leaks into the host
  // page.

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .root {
      position: fixed; right: 20px; bottom: 20px; z-index: 2147483647;
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      font-size: 14px; line-height: 1.5; color: #16181d;
    }
    .pill, .card {
      background: #fff; border: 1px solid #e7e5e0; border-radius: 10px;
      box-shadow: 0 4px 8px rgba(22,24,29,.06), 0 16px 48px rgba(22,24,29,.08);
    }
    .pill {
      display: flex; align-items: center; gap: 9px;
      padding: 9px 14px; cursor: pointer; user-select: none; white-space: nowrap;
    }
    .pill:hover { background: #f6f6f3; }
    .mark { font-weight: 600; letter-spacing: -.01em; }
    .mark i { color: #0b7a5b; font-style: normal; }
    .pill .cta { color: #5b616e; }
    .card { width: 340px; max-height: 70vh; overflow-y: auto; padding: 16px; }
    .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
    .x { border: 0; background: none; cursor: pointer; color: #8a909c; font-size: 18px; line-height: 1; padding: 0 2px; }
    .x:hover { color: #16181d; }
    .eyebrow {
      font-family: ui-monospace, SFMono-Regular, monospace; font-size: 10px;
      letter-spacing: .08em; text-transform: uppercase; color: #8a909c;
    }
    .verdict { font-size: 15px; font-weight: 500; margin: 2px 0 10px; }
    .badge {
      display: inline-block; padding: 2px 8px; border-radius: 6px;
      font-family: ui-monospace, SFMono-Regular, monospace; font-size: 10px;
      letter-spacing: .06em; text-transform: uppercase;
    }
    .badge.high { background: #e8f4ef; color: #0b7a5b; }
    .badge.moderate { background: #fbf1e5; color: #b45309; }
    .badge.low { background: #fbeceb; color: #c0362c; }
    .sec { margin-top: 12px; }
    .sec h4 { margin: 0 0 5px; font-size: 12px; font-weight: 600; color: #5b616e; }
    .sec ul { margin: 0; padding-left: 16px; }
    .sec li { margin-bottom: 4px; color: #16181d; }
    .flag li { color: #c0362c; }
    .foot {
      margin-top: 14px; padding-top: 10px; border-top: 1px solid #e7e5e0;
      display: flex; align-items: center; justify-content: space-between;
      font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11px; color: #8a909c;
    }
    .foot a { color: #2d6ba8; text-decoration: none; }
    .foot a:hover { text-decoration: underline; }
    .muted { color: #5b616e; }
    .err { color: #c0362c; }
    button.act {
      border: 1px solid #0b7a5b; background: #0b7a5b; color: #fff;
      padding: 7px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;
    }
    button.act:hover { background: #096b4f; }
    button.act:disabled { opacity: .55; cursor: default; }
    button.ghost { border: 1px solid #d8d5cd; background: #fff; color: #16181d; }
    button.ghost:hover { background: #f6f6f3; }
    .row { display: flex; gap: 8px; align-items: center; }
    .spin { display: inline-block; width: 11px; height: 11px; border: 2px solid #e7e5e0;
            border-top-color: #0b7a5b; border-radius: 50%; animation: s .7s linear infinite; }
    @keyframes s { to { transform: rotate(360deg); } }
  `;

  let host = null;
  let root = null;

  function mount() {
    if (host) return root;
    host = document.createElement("div");
    host.id = "laeria-overlay-host";
    root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = CSS;
    root.append(style, document.createElement("div"));
    root.lastElementChild.className = "root";
    document.documentElement.appendChild(host);
    return root;
  }

  const shell = () => mount().querySelector(".root");
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  function renderPill(label, onClick) {
    const el = shell();
    el.innerHTML = `<div class="pill"><span class="mark">laeria<i>.</i></span><span class="cta">${esc(label)}</span></div>`;
    el.querySelector(".pill").addEventListener("click", onClick);
  }

  function renderCard(inner) {
    const el = shell();
    el.innerHTML = `<div class="card">${inner}</div>`;
    el.querySelector(".x")?.addEventListener("click", () => host.remove(), { once: true });
    return el;
  }

  const list = (items, cls = "") =>
    items?.length
      ? `<ul class="${cls}">${items.slice(0, 3).map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`
      : "";

  function renderBrief(brief, product, cached) {
    const conf = String(brief.confidence || "low").toLowerCase();
    const pick = brief.consensus_pick || "No clear consensus in the threads found.";
    const q = encodeURIComponent(product.title);
    const el = renderCard(`
      <div class="head">
        <div>
          <div class="eyebrow">community verdict</div>
          <span class="badge ${esc(conf)}">${esc(conf)} confidence</span>
        </div>
        <button class="x" title="Close">&times;</button>
      </div>
      <div class="verdict">${esc(pick)}</div>
      ${brief.red_flags?.length ? `<div class="sec"><h4>Red flags</h4>${list(brief.red_flags, "flag")}</div>` : ""}
      ${brief.failure_modes?.length ? `<div class="sec"><h4>How it goes wrong</h4>${list(brief.failure_modes)}</div>` : ""}
      ${brief.alternatives?.length ? `<div class="sec"><h4>Also mentioned</h4>${list(brief.alternatives)}</div>` : ""}
      <div class="foot">
        <span>${brief.sources?.length || 0} threads${cached ? " · cached" : ""}</span>
        <a href="https://laeria-ai.vercel.app/decision?q=${q}" target="_blank" rel="noreferrer">full report ↗</a>
      </div>
    `);
    return el;
  }

  function renderError(message, retry) {
    const signedOut = message === "NOT_SIGNED_IN";
    renderCard(`
      <div class="head">
        <div class="eyebrow">laeria</div>
        <button class="x" title="Close">&times;</button>
      </div>
      <div class="${signedOut ? "muted" : "err"}">${
        signedOut
          ? "Sign in from the laeria toolbar icon to see community verdicts."
          : esc(message)
      }</div>
      ${signedOut ? "" : `<div class="sec"><button class="act" id="retry">Try again</button></div>`}
    `);
    shell().querySelector("#retry")?.addEventListener("click", retry);
  }

  // ---- flows ------------------------------------------------------------

  const send = (msg) =>
    new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));

  let progressEl = null;

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg?.type === "RESEARCH_PROGRESS" && progressEl) {
      progressEl.textContent = `reading threads… ${msg.seconds}s`;
    }
  });

  async function research(product, force = false) {
    renderCard(`
      <div class="head"><div class="eyebrow">laeria</div><button class="x" title="Close">&times;</button></div>
      <div class="row"><span class="spin"></span><span class="muted" id="p">asking the communities…</span></div>
      <div class="sec muted" style="font-size:12px">Reading real threads takes 30–90 seconds. You can keep browsing.</div>
    `);
    progressEl = shell().querySelector("#p");

    const res = await send({ type: "RESEARCH", product, force });
    progressEl = null;
    if (!res) return renderError("extension did not respond", () => research(product, force));
    if (!res.ok) return renderError(res.error, () => research(product, true));
    renderBrief(res.data.brief, product, res.data.cached);
  }

  async function importOrder(order) {
    renderCard(`
      <div class="head"><div class="eyebrow">laeria</div><button class="x" title="Close">&times;</button></div>
      <div class="row"><span class="spin"></span><span class="muted">setting up monitoring…</span></div>
    `);
    const res = await send({ type: "IMPORT_ORDER", items: order.items });
    if (!res?.ok) return renderError(res?.error || "import failed", () => importOrder(order));

    const { created, failed } = res.data;
    renderCard(`
      <div class="head">
        <div class="eyebrow">now monitoring</div>
        <button class="x" title="Close">&times;</button>
      </div>
      ${
        created.length
          ? `<div class="sec">${created
              .map(
                (c) =>
                  `<div style="margin-bottom:6px"><div>${esc(c.name)}</div><div class="eyebrow">${c.subreddits
                    .map((s) => "r/" + esc(s))
                    .join(" · ")}</div></div>`
              )
              .join("")}</div>`
          : `<div class="muted">Nothing could be set up.</div>`
      }
      ${failed.length ? `<div class="sec"><h4>Skipped</h4>${list(failed.map((f) => `${f.name} — ${f.error}`))}</div>` : ""}
      <div class="foot">
        <span>checks every 24h</span>
        <a href="https://laeria-ai.vercel.app/monitor" target="_blank" rel="noreferrer">open monitor ↗</a>
      </div>
    `);
  }

  // ---- entry ------------------------------------------------------------

  function start() {
    const order = detectOrder();
    if (order) {
      const n = order.items.length;
      return renderPill(`Monitor ${n} item${n === 1 ? "" : "s"} you just bought`, () =>
        importOrder(order)
      );
    }
    const product = detectProduct();
    if (product) {
      return renderPill("Is it worth it?", () => research(product));
    }
  }

  // Shops render late and navigate without reloading, so re-check on history
  // changes as well as at idle. Cheap: detection is DOM reads only.
  let lastUrl = location.href;
  start();
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      host?.remove();
      host = null;
      root = null;
      setTimeout(start, 800);
    }
  }, 1000);
})();
