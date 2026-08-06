# laeria browser extension

Two things the web app cannot do, because both need the page you are looking at:

1. **Verdict on a product page.** Detects that a page is a product, and asks the
   existing research pipeline what the communities actually say. No typing.
2. **Order import.** On an order confirmation, offers to monitor what you just
   bought — which is what Mode 3 has always needed and never had, since nobody
   hand-types a list of things they own.

Together they bookend the judged flow: discovery starts in the browser, and
after the agent checks out, the order confirmation feeds monitoring.

## It is a thin client

All research, mandate and payment logic stays on the backend. The extension
detects, renders, and asks. Specifically:

- `content.js` runs inside shop pages and holds **no credentials**. It reads the
  DOM and posts messages.
- `background.js` is the only place with a token. Fetches happen there, which
  also means MV3 `host_permissions` cover them and no backend CORS change was
  needed.
- The overlay renders in a **shadow root**, so hostile shop CSS cannot reach it
  and its styles cannot leak out.

## Install (unpacked)

Not published to the Chrome Web Store, deliberately — review takes weeks and
gates nothing. For the demo, load it directly:

1. `chrome://extensions`
2. Turn on **Developer mode**
3. **Load unpacked** → select this `extension/` folder
4. Click the laeria toolbar icon → **Connection settings**

Fill in the three public values, the same ones the web app ships in its client
bundle (`frontend/.env.local`, or the Vercel project's environment):

| Field | From |
|---|---|
| Backend URL | `NEXT_PUBLIC_API_URL` |
| Supabase URL | `NEXT_PUBLIC_SUPABASE_URL` |
| Supabase anon key | `NEXT_PUBLIC_SUPABASE_ANON_KEY` |

Then sign in with the same account you use on the web app. Research is open to
any signed-in account, so this does not need the owner login.

You can also pre-fill the defaults in `config.js` instead of typing them.

## Using it

**Product pages** — a `laeria.` pill appears bottom right. Click it. Research
runs as a polled job (30–90s) and returns consensus, red flags, failure modes
and alternatives, with a link to the full report.

The page title is **not** sent as the query. Titles are written for search
engines; Reddit search is literal keyword matching, so a title like *"Keychron
K2 HE Wireless Magnetic Switch Custom Keyboard"* matches almost no real thread,
the backend falls into its no-candidates retry, and you wait roughly twice as
long for a low-confidence answer. `cleanQuery()` cuts the title to the part that
names the thing — separator suffixes, bracketed asides and marketing vocabulary
go, and the result is capped at six words:

```
Keychron K2 HE Wireless Magnetic Switch Custom Keyboard → Keychron K2 HE Magnetic Switch Keyboard
Men's Tree Runner NZ - Medium Grey (Blizzard Sole)      → Men's Tree Runner NZ
Anker 737 Power Bank (PowerCore 24K) — 24,000mAh …      → Anker 737 Power Bank
```

The same cleaned string is what the *full report* link carries, so the web app
hits the backend's 24h cache instead of researching from cold.

Verdicts are cached locally for 12 hours keyed on the product title, so
revisiting a page is instant. The backend caches for 24h independently.

**Order confirmations** — the pill offers to monitor the items. For each one it
calls `GET /research/subreddits` to plan communities, then creates a monitored
item on a 24-hour interval.

## Detection

Product, in priority order:

1. JSON-LD `Product` — what the merchant asserts the page *is*
2. OpenGraph `og:type=product` or `product:price:amount`
3. URL shape (`/products/<handle>`) and a rule for Amazon

The order matters: category and search pages match a `/products/` path too, so
URL shape is the last resort rather than the first check.

Orders: JSON-LD `Order` anywhere, plus Shopify thank-you pages
(`/thank_you`, `/orders/<token>`, `[data-step="thank_you"]`).

Shops render late and often navigate without a reload, so detection re-runs on
URL change. It is DOM reads only, so this is cheap.

## Known limits

- **`content_scripts` matches `http://*/*` and `https://*/*`.** Broad, and a
  review flag if this is ever submitted to the Web Store. Narrowing it to a
  merchant allowlist would be the first change before publishing.
- **Shopify thank-you selectors are markup-dependent.** They are checked
  against the demo store; a different theme may need different ones. The
  JSON-LD `Order` path is the durable one.
- **Prices are read but not used.** Detection extracts them for later — the
  verdict is keyed on the title alone today.
- **No payment surface.** Buying stays in the web app, owner-only, for the same
  reason it always has: the wallet, cardholder and storefront session are
  single global instances.
