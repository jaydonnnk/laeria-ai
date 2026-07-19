# Session Transfer

## Completed
- All planned phases (0–5) of baryon.ai built and committed: repo scaffold, old.reddit HTML scraping (Reddit's free API is dead), Mode 2 decision synthesis, Mode 1 retrospective mining, Mode 3 monitoring (worker + alert engine + Obsidian sync), real x402 payments on Base Sepolia (official Coinbase SDK, on-chain settled), Supabase Auth, action triggers wiring Mode 2→buy and Mode 3→cancel/replace, x402 Bazaar discovery (real third-party paid services), Phase 5 hardening (embedding-based anti-shill analysis, usage tracking).
- Tester audit completed: found and fixed a **critical mandate-bypass bug** (failed price discovery priced a purchase at $0, letting every spend cap pass vacuously before the real vendor price was charged). Fixed with a discovery-failure refusal + execution-time price re-verification + hard payment ceiling.
- Fixed 4 smaller issues: worker duplicate-instance risk (port-lock singleton), Reddit request pacing being per-instance instead of shared (concurrent runs could double the request rate), missing logout button, mandate UI not explaining that `0` means "no cap" not "$0 cap".
- Ran a 15-test automated sweep (auth isolation with a real created-then-deleted intruder Supabase user, all 4 mandate refusal paths, reject flow, approval expiry, zero-coverage research paths, delete cascade, Obsidian writes, singleton lock) — all passed.
- Fixed a same-day Windows environment break: Smart App Control started blocking bitarray's C extension (a transitive eth_account dependency pulled in only by its unused HD-wallet/mnemonic path), which killed wallet signing. Replaced with a pure-Python bitarray reimplementation (no native DLL, nothing for SAC to block) at `infra/bitarray_stub/`, copied into the venv. Re-verified full on-chain x402 settlement afterward (new tx confirmed).
- Live-validated the Obsidian vault-suggest flow with real vault content: correctly excluded a "thinking about buying" item, correctly extracted real owned items already present in the user's own notes (not just the seeded test note).

## Decisions
- **HTML scraping over Reddit API** — Reddit's free JSON API now 403s and OAuth script-app approval is gated behind a manual review process with no clear timeline. old.reddit.com HTML scraping (with browser UA, paced requests) was chosen as the practical alternative.
- **Monitor worker runs on the user's laptop, not the VPS** — the Hetzner VPS's datacenter IP is blocked by Reddit entirely (tested directly, 403 on every request). Laptop has a residential IP that works. `REDDIT_PROXY` config was added so switching to VPS + a paid residential proxy later is a one-line change, but no proxy has been purchased.
- **Real x402 (not a mocked/demo payment scheme)** — user explicitly requested real payments over a demo. Using Coinbase's official `x402` Python SDK on Base Sepolia testnet with the free hosted facilitator (x402.org), which provides genuine EIP-712 signing and on-chain settlement at zero cost. Production only requires switching one network env var and funding the wallet with mainnet USDC.
- **Bazaar discovery over building more demo infrastructure** — rather than expanding the self-hosted demo vendor, wired up Coinbase's public Bazaar index (25k+ real live x402 services) so the agent can discover and pay genuine third-party services, not just its own test endpoint.
- **Single-user "owner" model instead of multi-tenant auth** — `OWNER_USER_ID` env var + Supabase Auth token check restricts all API access to one specific user ID. Appropriate for a personal tool; no user management UI exists.
- **Cancel-subscription actions execute as "log + Obsidian reminder", not an API call** — no consumer subscription service exposes a cancellation API, so the honest implementation records the decision and reminds the user to manually finish it, rather than pretending to automate something impossible.
- **Windows Smart App Control workaround via pure-Python reimplementation, not disabling SAC** — disabling a Windows security feature system-wide was rejected in favor of removing the one specific unsigned binary dependency that isn't actually needed for the app's real usage (raw-key signing, not BIP39 mnemonics).

## Traps
- **Reinstalling `bitarray` normally will silently restore the SAC-blocked C extension** and break wallet signing again. The pure-Python stub at `infra/bitarray_stub/bitarray/` must be copied over `.venv/Lib/site-packages/bitarray/` after any venv rebuild — `pip install bitarray` alone will re-break it. This will very likely bite a future session that runs a routine `pip install -r requirements.txt` on this Windows machine without knowing this.
- **The monitor worker auto-starts via a Windows Startup shortcut (`baryon-monitor.lnk`)** pointing at `run_worker.cmd`. It is easy to forget this exists and accidentally launch a second instance manually — the port-lock singleton guard (bound to localhost:47931) now prevents actual duplicate execution, but don't assume the worker isn't already running.
- **A quarantined `.vbs` launcher may still sit in the Windows Startup folder** from an earlier abandoned approach (Windows Defender flagged `.vbs` startup scripts as suspicious). It is inert but will trigger a Windows Script Host warning popup at every login until manually removed via Windows Security → Protection history.
- **Mandate fields default to `0`, which means "no cap", not "$0 allowed."** This was the source of the critical bypass bug context — always check `autonomous_actions_enabled` and non-zero caps together, never assume `0` is a safe/restrictive default.
- **The `.venv` lives inside a OneDrive-synced folder.** OneDrive has previously interfered with file locking during dependency installs. If install/build steps behave strangely, suspect OneDrive sync contention before assuming a code bug.

## Working Agreements
- User wants terse, direct answers (caveman-mode communication style is active this session) — no filler, no hedging, get to the point.
- User explicitly controls phase pacing: work stops and waits for an explicit go-ahead between major phases/features rather than the agent self-continuing through a roadmap.
- User pushed back twice to get more real implementations instead of shortcuts: once to make x402 real (not a demo/mocked payment), and once to discuss AP2/x402 feasibility before accepting a scoped-down plan — the agent should default to proposing the most real/production-faithful implementation and let the user opt into simplification, not the reverse.
- When infrastructure/deployment problems appear (VPS Reddit block, Windows SAC block), the user prefers a working local/practical fallback now over blocking on a full "correct" fix — but wants the fallback clearly labeled as a fallback with a real path back to the ideal state documented.
- User is running manual verification passes personally (clicking through the UI) after the agent's automated backend-level test sweeps, rather than trusting automated tests alone for anything payment- or UI-related.
- After a bug-fixing/hardening pass, the user wants an explicit "what's left for me to test/fix manually" list at the end, not just a summary of what was done.

## Files Changed
Extensive multi-session changes across the whole `backend/` and `frontend/` trees (16+ commits). Most significant recent changes:
- `backend/api/routes/actions.py` — added execution-time price re-verification and mandate re-check inside `_execute()` (the critical bypass fix); `check_402` failure now raises instead of defaulting to a $0/free price.
- `backend/services/payment.py` — `pay_402` given a `max_amount_usd` hard-ceiling parameter that raises `MandateViolation` if the vendor's actual price exceeds the approved amount.
- `backend/workers/monitor_worker.py` — added `_acquire_singleton_lock()` (binds `127.0.0.1:47931`) called at worker startup; process exits immediately if the port is already held.
- `backend/services/reddit.py` — request pacing changed from an instance attribute to a shared/class-level pacer so concurrent `RedditService` instances don't multiply request rate.
- `frontend/` — added a sign-out button (exact page not confirmed in this summary) and a mandate UI note clarifying that `0` in a cap field means unlimited.
- `infra/bitarray_stub/bitarray/__init__.py`, `infra/bitarray_stub/bitarray/util.py` — new pure-Python `bitarray` reimplementation (class + `ba2int`/`int2ba`) replacing the SAC-blocked native package in the local venv only.
- `SESSION_TRANSFER.md` — this file, newly created at the project root.

## Open Work
- **User is mid-way through a manual browser test checklist** the agent provided (5 items: sign-out button, Mode 2 buy-button flow, approve/reject actions flow, monitor item delete, Obsidian vault-suggest → approve → check-now flow). No results have been reported back yet — status of each of these 5 items is unknown until the user reports back.
- VPS deployment of the monitor worker is not started — blocked on the user purchasing a residential proxy (no proxy purchased yet).
- Production mainnet x402 payments are not enabled — currently testnet-only (Base Sepolia); switching requires a network env var change and funding the wallet with real mainnet USDC, neither done.
- No CI/tests-on-push exists; all verification so far has been manual/scripted one-off runs during the session, not a persistent test suite.
- The quarantined `.vbs` file in the Windows Startup folder has not been confirmed removed.

---

## Prompt for New Chat

This is a continuation of work on **baryon.ai**, a personal Reddit-signal-driven research/monitoring/payment agent at `C:\Users\jayd0\OneDrive\Desktop\baryon.ai`. All planned phases (0 through 5) are built and committed to git: Reddit HTML scraping (the free Reddit API is defunct), decision-synthesis research (Mode 2), retrospective-outcome research (Mode 1), item monitoring with an alert engine (Mode 3), a monitor worker, Obsidian vault integration, real x402 crypto payments on Base Sepolia testnet via Coinbase's official SDK (on-chain settlement verified working), Supabase Auth restricting the app to a single owner user, action triggers connecting research/monitoring outputs to payment actions, x402 Bazaar discovery of real third-party paid services, and Phase 5 hardening (embedding-based anti-shill/duplicate-content detection, usage tracking).

A tester-style audit pass was just completed. It found and fixed a critical bug where a failed price-discovery request would let a purchase bypass all spending-cap checks (now fixed with execution-time re-verification and a hard payment ceiling), plus four smaller fixes (worker singleton lock, shared Reddit request pacing, a missing sign-out button, and a mandate UI clarification that a `0` cap value means unlimited, not zero-dollar). A 15-test automated sweep of backend/API-level behavior passed. Separately, Windows Smart App Control started blocking a native dependency (`bitarray`, pulled in transitively by an unused wallet feature) partway through the session, breaking payment signing; this was fixed by swapping in a pure-Python reimplementation at `infra/bitarray_stub/`, and on-chain payment settlement was re-verified working afterward.

The monitor worker currently runs on the user's laptop (not the VPS) because the VPS's datacenter IP is blocked by Reddit entirely — this is a known, deliberate, documented interim state, not a bug to fix. Similarly, x402 payments are on testnet, not mainnet, by deliberate choice pending the user funding a real wallet.

The user was given a 5-item manual browser test checklist to click through (sign-out, Mode 2 buy button, approve/reject payment actions, monitor item deletion, and the Obsidian vault-suggest-then-approve-then-check-now flow) and has not yet reported results back.

Wait for instructions before taking any action.
