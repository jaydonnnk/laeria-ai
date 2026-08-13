# Session Transfer

Session of 2026-08-12/13. Started at `d14bdb1`, ends at `29cdda1` (10 commits,
~2,800 lines). **All pushed** — `origin/master..master` is empty.

120 backend tests pass. Frontend builds clean; `/decision` still prerenders static.

## Completed

- **Strict gap analysis against the four judged milestones**, which found the
  root problem the rest of the session addressed: the crypto rail and the card
  rail shared an `ActionMandate` object and nothing else. No money moved
  between them, so "XSGD funds an agentic purchase" was two demos standing
  next to each other.
- **The card is now backed by the wallet.** The agent's on-chain balance
  joined the `headrooms` list the card ceiling is the `min()` of. Proven by
  the run that exposed it: a $46.20 card had been issued against $19.94 of
  backing.
- **Purchases settle on-chain.** A completed order transfers its total from
  agent to merchant and the tx hash lands on the receipt beside the order
  reference.
- **XSGD is live on Avalanche Fuji** at
  `0x4C41454F440a5869cb881895C26dB9d15Ab65cfA` — symbol `XSGD`, 6 decimals,
  1,000,000 minted to treasury. Deployed via `scripts/deploy_xsgd.py`.
- **Avalanche is the default, not the swap.** Base Sepolia and Base mainnet
  removed from `_NETWORKS`; Fuji is the default network with the XSGD stand-in
  as its default token, so the standard demo needs no `STABLECOIN_*` env vars.
- **`scripts/check_chain.py`** — six preconditions (right chain, token
  identity, symbol agreement, gas per wallet named by what it unblocks,
  facilitator support). Currently reports `CHAIN READY`.
- **Discovery is agentic** (`agents/shopping_agent.py`). Free-text instruction
  → model-derived query and budget → real browser search of the shop's own
  search page → model picks with a reason and a rejected list. Measured 20s
  end to end against the live store; picks the ski wax at 24.95 under a $30
  budget with the correct variant id.
- **Reddit diagnosed and made honest.** `old.reddit.com` now 403s every
  logged-out request. The pipeline is fine — a recorded query still returns
  MODERATE confidence over 8 threads.
- Real XSGD verified on-chain at `0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E`
  (Avalanche C-Chain mainnet, symbol XSGD, 6 decimals). Config and
  `.env.example` previously said "reportedly".
- Docs rewritten for the above: `HACKATHON_SWAP.md`, `DEMO_SCRIPT.md`,
  `README.md`.

## Decisions

- **The Shopify storefront stays.** Deleting it was requested, then withdrawn
  once the constraint was clear: a sandbox-issued card only authorises against
  a gateway we control, and real merchants add 3DS plus bot defences. The
  storefront is not scaffolding — it is the only place the card can clear.
- **Mainnet AVAX purchased to unlock the faucet** rather than asking the
  community. The user declined the community route as too uncertain to
  schedule around. Faucet gate confirmed live before spending: a coupon *or*
  any nonzero mainnet C-Chain balance, no threshold.
- **A self-deployed Fuji stand-in over mainnet read-only.** XSGD exists only
  on Avalanche mainnet, where `_transfer` refuses by design, so the only chain
  carrying the asset is the one chain the funding leg cannot run on. Framed as
  a rehearsed config swap, not an integration — and the mainnet contract is
  read live on stage to demonstrate rather than assert it.
- **Deploy from a script, not Remix.** Remix needs four things to agree before
  a byte compiles; its Environment silently defaulted to WalletConnect, whose
  hardcoded chain list has no Avalanche at all.
- **The contract dropped its OpenZeppelin import.** That import is what forces
  npm, a remapping and a working Remix session into the deploy path. Everything
  the backend calls is `transfer`/`balanceOf`/`decimals`/`symbol`.
- **Assume StraitsX supplies build ideas and nothing else.** Their public
  product is a Cards sandbox, not a token faucet; XSGD is regulated e-money
  whose proposition is a reserve a testnet version would not have; and anything
  arriving Friday night is a hope, not a plan.
- **Base removed entirely rather than demoted.** Requested. Consequence
  recorded in `HACKATHON_SWAP.md`: there is no longer a Base Sepolia fallback,
  and the doc previously instructed the presenter to use one.
- **Discovery scans the browser, with the JSON catalogue as fallback** (the
  "Option B" of two offered). Matches the milestone wording literally and lets
  the audience watch the agent search. A degraded run is badged
  "catalogue fallback" rather than presented as a scan.
- **Settlement never raises.** It runs after the order exists, so a chain
  failure is a receipt field reading "ordered, not settled on-chain", not an
  exception that would relabel a bought item as a crash.
- **`py-solc-x` deliberately out of `requirements.txt`** — the service image
  has no reason to carry a Solidity compiler.

## Traps

- **`rtk` is not installed in this environment.** The global CLAUDE.md
  prescribes prefixing every command with it; doing so fails with
  `rtk: command not found`. Use the bare command.
- **web3's `build_transaction` fills the gas limit from the account balance
  when the gas price is near zero.** Observed on Fuji (base fee 10 wei):
  3.1e15 against a true estimate of 420,480, eight orders past the 32M block
  limit, so the node rejects it. Estimate explicitly and floor the price. The
  temptation is to read the absurd number as an RPC bug — `eth_gasPrice`
  returning 160 wei is genuine.
- **Shopify serves the same product three ways and they disagree.**
  `/products.json` has `available`; `/products/{h}.json` **omits** it;
  `/products/{h}.js` has it and prices in **cents**. A lookup added to fix
  pagination used the middle one, so every product read as sold out and the
  shopping agent refused to buy anything. Also: `.js` top-level `price` is the
  *cheapest* variant, not `variants[0]`.
- **`X402_NETWORK` must be flipped BEFORE deploying** — it selects the chain
  deployed to. `STABLECOIN_CONTRACT` can only be set after. The walkthrough
  originally had both after, and the first real deploy attempt pointed at Base
  Sepolia and refused for lack of gas.
- **MetaMask's "Import account" does not exist during onboarding.** A wallet
  shell must be created first; "I have an existing wallet" wants a seed
  phrase, not a private key. The seed backs up only Account 1, never the
  imported account.
- **Exchange withdrawals must select Avalanche C-Chain**, not X-Chain — the
  faucet reads C-Chain only.
- **Reddit: exactly three query strings work, byte-exact.** The research
  *plan* is part of the recorded corpus, only three were captured, and the
  fixture key is a raw SHA-256 of the query with no normalisation, so
  capitalisation and `$100` both matter. The temptation is to debug the
  pipeline — it is fine.
- **`DEMO_SCRIPT.md`'s trap list said mandate `0` means "no cap".** The exact
  inversion already fixed once in the checklist above it, and it would have
  refused every purchase on stage for a second time.
- **Two claims made this session were wrong and corrected by measurement.**
  `REDDIT_SOURCE=fixture` was described as faster — measured 48.3s vs 49.5s,
  noise, because the doomed fetches overlap. And the checkout was described as
  never having run end to end, taken from a stale `SESSION_TRANSFER` line;
  `screenshots/checkout/` holds three complete sets from Jul 20.
- **`demo_e2e --buy` places a real order** on the live Shopify store. It is
  not a test double — same code path as the UI, minus the approval pause,
  which it skips by temporarily raising the mandate.
- **`laeria-ai.vercel.app` is a separate deployment.** Local `.env` edits do
  not reach it; its backend is Render. The demo runs locally because
  `BROWSER_HEADED=true` is meaningless in a container.

## Working Agreements

- **Root cause over symptom, stated explicitly.** The user asks directly
  whether a fix addresses architecture or papers over it, and expects the
  distinction called out per item.
- **Commits carry no `Co-Authored-By` trailer.** The user presses Sync in a
  GUI — commit locally, never push.
- **Judgement calls are delegated, then verified.** "Make ur judgement call"
  is common; the expectation is a decision plus the reasoning that would
  change it, not a menu.
- **Say plainly what is the user's job and what is the agent's.** Purchases,
  KYC, CAPTCHAs and Shopify admin are theirs; they ask for that split.
- **Explanations get flagged when too dense.** "Hard to understand" was said
  once; the rewrite that worked led with the human-level problem and kept code
  identifiers out of the explanation.
- **Measure rather than assert.** Both corrections above came from being asked
  to verify a claim that had been stated confidently.

## Files Changed

Backend:
- `services/wallet.py:1-120` — module docstring and `_NETWORKS` rewritten;
  Base entries removed, Fuji default token is the XSGD stand-in, Avalanche
  mainnet carries real XSGD. `:98-125` — new `is_testnet()` and
  `configured_token_decimals()`. `:167-215` — `_token_decimals` reads
  `decimals()` off the contract, uncached on failure. `:228-270` — new
  `chain_id`, `expected_chain_id`, `probe_token`, `native_balance`;
  `usdc`/`usdc_contract` keys removed from `balances()`. `:280-400` — transfers
  collapsed into one `_transfer()` carrying the mainnet refusal; `fund_agent`
  rewritten onto it; new `settle_purchase()`.
- `api/routes/actions.py:56-100` — new `_stablecoin_backing()`. `:102-125` —
  new `_settle_on_chain()`, never raises. `:290-300` — backing check inside the
  execute-time recheck. `:330-345` — `headrooms.append(backing)`. `:400-420` —
  settlement call and `settlement` metadata. `:470-478` — propose uses
  `get_product` instead of scanning a paginated listing.
- `services/storefront.py:126-200` — new `browser_search()`,
  `_handle_from_href()`, `get_product()` on `.js`, `_parse_product_js()`.
- `agents/shopping_agent.py` — new, 275 lines.
- `services/checkout.py:83-115` — currency-agnostic `_money_after`.
- `services/payment.py:1-20, 128-155` — docstring, and the mainnet guard now
  derives from `is_testnet` rather than Base's chain id. `:360-375` —
  `_requirement_amount_usd` uses configured decimals, not `1e6`.
- `services/reddit.py:64-78` — new `FixtureMissing`. `:218-265` — new
  `probe_live()`; `healthcheck` documents which source satisfied it.
  `:349-360, 529-540, 546-556` — `FixtureMissing` caught alongside HTTP errors.
- `agents/research_agent.py:199-210` — new `NoRecordedPlan`. `:228-245` —
  plan miss degrades instead of 500ing. `:280-300` — empty-result note names
  the Reddit block.
- `scripts/check_chain.py`, `scripts/deploy_xsgd.py` — new.
- `scripts/demo_e2e.py:54-90` — chain preflight replaces the RPC healthcheck.
- `core/config.py:79-86` — Avalanche defaults. `:100-112` — new
  `merchant_settlement_address`.
- `tests/` — five new files: `test_checkout_money`, `test_wallet_decimals`,
  `test_stablecoin_backing`, `test_settlement`, `test_shopping_agent`,
  `test_storefront_parse`. `test_environment.py:36-50` — Reddit split into
  live-access (soft) and can-serve (hard).

Frontend:
- `app/commerce/page.tsx:34-36, 119-146` — shopping-agent state and handlers.
  `:200-235` — agent instruction form, manual search demoted to `<details>`.
  `:600-680` — new `SettlementLine` and `ShopResult`. `:455-470` — amount field
  denominated in the token symbol.
- `lib/api.ts:261-275` — `storeShop`. `:303-320` — `usdc` keys removed.
  `:389-420` — `ShopPick`/`ShopRejection`.
- `app/actions/page.tsx:126, 379-383` — XSGD on Avalanche; Bazaar note reframed
  as third-party chains.

Infra: `infra/contracts/XSGDTest.sol` — new, dependency-free ERC-20.

## Open Work

- **Fund and Settle have never executed on Avalanche.** `check_chain` proves
  the chain is readable, which is a different claim from a transfer signing and
  confirming. Settlement has never run against any real chain — it shipped with
  unit tests only. This is the largest untested surface and it sits on the two
  highest-weighted milestones. Blocks confidence in Milestones 1 and 4.
- **The shopping agent has not been exercised through the UI.** Verified via
  direct calls against the live store only; the `/commerce` instruction box,
  the reason/rejected rendering and the "Buy this" handoff into `propose` are
  untested in a browser. Depends on nothing.
- **Step 6 — SGD store pricing and mid-band products.** Shopify admin work,
  the user's. Not started. The store still prices in USD while the agent is
  funded in XSGD, and the catalogue jumps $24.95 → $600 with no product in the
  approval-beat band. The code side is done: the checkout money parser is
  currency-agnostic and tested.
- **Step 7 — the honesty beat about the PAN shim.** The user's. Not started.
  `DEMO_SCRIPT.md` carries the wording.
- **Step 8 — own the checkout gateway** so the issued card is the card that
  pays. Optional, not started, explicitly gated behind 6 and 7.
- **Render env vars.** The user was updating them when the session ended;
  unconfirmed. The deployed site showed Base Sepolia and USDC before that.
- **Reddit access remains unsolved.** The durable fix is the official API under
  OAuth; the credential fields exist in config and are unused, since the
  service is HTML-only. Not a judged milestone.

---

## Prompt for New Chat

This continues work on **laeria.ai** at
`C:\Users\jayd0\OneDrive\Desktop\laeria.ai` — an agent that researches
purchases from Reddit consensus and completes them under a spending mandate.
It is the submission for the **StraitsX Agentic Playground Hackathon**
(Singapore, 14–16 Aug 2026, submission Sunday 1100). StraitsX hosts; Avalanche
is title partner. Kite is not involved and must not be mentioned.

Four judged milestones: Funding, Discovery, Issuance, Execution. All four are
implemented. Everything is committed and pushed at `29cdda1`; 120 backend
tests pass and the frontend builds clean.

The session began as a gap analysis and found one root problem: the crypto rail
and the card rail shared a mandate object and nothing else, so no money moved
between them. Two changes closed it. The agent's on-chain balance is now one of
the ceilings the disposable card's limit is the minimum of, so a card cannot be
issued for more than the wallet holds. And a completed order settles on-chain,
agent to merchant, with the tx hash on the receipt beside the order reference.
Settlement deliberately never raises — it runs after the order exists, so a
chain failure reads "ordered, not settled on-chain" rather than relabelling a
bought item as a crash.

The chain moved from Base Sepolia to Avalanche. XSGD has no public Fuji
deployment, so a stand-in was deployed at
`0x4C41454F440a5869cb881895C26dB9d15Ab65cfA` — same symbol, same 6 decimals,
source in `infra/contracts/XSGDTest.sol`, deployed by
`scripts/deploy_xsgd.py` rather than Remix. Real XSGD was verified on-chain at
`0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E` on Avalanche mainnet, where
`_transfer` refuses by design, making that entry a read-only window. Base
Sepolia and Base mainnet were removed from `_NETWORKS` entirely, so there is no
longer a Base fallback. `scripts/check_chain.py` currently reports
`CHAIN READY` across six preconditions.

Discovery was rebuilt as `agents/shopping_agent.py`. A free-text instruction
becomes a query and a budget via one model call, a real browser searches the
shop's own search page, and a second model call picks with a reason and a
rejected list. Three guards exist because a model choosing purchases is new
risk: a handle absent from the results is refused rather than fuzzy-matched,
the budget is re-checked in code after the model answers, and a failed pick
does not fall back to first-in-stock. The pick is a proposal — the mandate
still decides whether it executes. Measured at 20 seconds against the live
store.

Reddit now 403s every logged-out request; the shutdown announced 2026-06-30 has
landed. The pipeline itself is unaffected — a recorded query still returns
MODERATE confidence over eight threads — but only three exact query strings are
in the corpus, and the fixture key is a raw SHA-256 of the query, so they must
be typed byte-exactly. `DEMO_SCRIPT.md` lists them.

Fund and Settle have never executed on Avalanche, and settlement has never run
against any real chain. The shopping agent has not been driven through the UI.
Steps 6 (repricing the Shopify store in SGD and adding products in the
approval-beat band) and 7 (declaring the Bogus-Gateway PAN shim on stage) are
the user's and have not been started. Render's environment variables were being
updated as the session ended and are unconfirmed.

Two claims made during the session were wrong and corrected by measurement:
`REDDIT_SOURCE=fixture` is not faster than `live_then_fixture`, and the
checkout had in fact run end to end in July. The user asks for root-cause
versus symptom to be named explicitly, delegates judgement calls and expects a
decision with its reasoning rather than a menu, wants the split between their
work and the agent's stated plainly, and commits without a `Co-Authored-By`
trailer — pushing is done by the user through a GUI Sync button.

Note that the global CLAUDE.md prescribes prefixing commands with `rtk`, which
is not installed in this environment and fails with `command not found`.

Wait for instructions before taking any action.
