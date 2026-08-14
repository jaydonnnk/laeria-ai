"""Centralised configuration loaded from environment.

All settings are read once at import via a cached Settings instance.
Nothing else in the codebase should read os.environ directly.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_anon_key: str = ""

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-haiku-4.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Per-request ceiling. The OpenAI SDK defaults to 600s, which turns an
    # intermittent stall into a ten-minute hang indistinguishable from slow
    # work.
    #
    # 45s, not 120s, because the timeout is multiplied by the retry count and
    # measurements put a healthy synthesis call at 18-25s. A call still
    # running at 45s is not slow, it is stuck: benchmarked against the real
    # prompt, one tail event took 287 SECONDS to return. Failing fast lets
    # _synthesise fall back to half a brief in reasonable time, which is what
    # that fallback is for.
    llm_timeout_seconds: int = 45
    # Attempts BEYOND the first. Each one costs another full timeout, so this
    # and llm_timeout_seconds multiply: at the old 120s/2 a single synthesis
    # half could occupy six minutes on its own.
    llm_max_retries: int = 1

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "laeria.ai/0.1"
    reddit_username: str = ""
    reddit_password: str = ""
    # Optional egress proxy for Reddit requests. Datacenter IPs (Hetzner etc.)
    # are 403-blocked by Reddit; a residential/rotating proxy makes VPS-hosted
    # scraping work. Empty = direct connection (fine on a residential IP).
    # Format: "http://user:pass@host:port" or "socks5://host:port".
    reddit_proxy: str = ""
    # Where Reddit HTML comes from: "live" | "record" | "fixture" |
    # "live_then_fixture". Reddit is closing logged-out old.reddit access
    # (announced 2026-06-30, rolling out over the following month), so a live
    # fetch can no longer be assumed. Demos should run "live_then_fixture".
    # See services/reddit_fixtures.py.
    reddit_source: str = "live_then_fixture"
    # How long a completed research brief stays reusable. The threads behind a
    # settled purchase question do not change hour to hour, and a repeat query
    # otherwise costs ~20 paced Reddit requests plus two LLM calls to produce
    # the same answer. 0 disables the cache.
    research_cache_seconds: int = 86_400

    # Obsidian
    obsidian_api_url: str = "https://127.0.0.1:27124"
    obsidian_api_key: str = ""
    obsidian_verify_ssl: bool = False

    # Owner (single-user mode until auth lands): UUID of the Supabase auth
    # user that owns all monitored items/alerts. Create one in the dashboard
    # (Authentication -> Users -> Add user) and paste its id here.
    owner_user_id: str = ""
    # Seconds to trust a previously-validated bearer token before re-checking
    # it with Supabase. Bounded by the token's own expiry, so this can only
    # shorten a session, never extend one. 0 disables caching.
    auth_cache_seconds: int = 300

    # x402 — real protocol on Avalanche Fuji, the chain this build showcases.
    # The facilitator MUST match the network: x402.org serves Base Sepolia
    # only, and Ultravioleta DAO serves Fuji (probed: x402Version 2, scheme
    # "exact", eip155:43113). Changing one without the other leaves payments
    # failing at settle time with an error that does not name the cause.
    x402_network: str = "eip155:43113"
    x402_facilitator_url: str = "https://facilitator.ultravioletadao.xyz"
    x402_agent_private_key: str = ""
    x402_agent_address: str = ""
    x402_treasury_address: str = ""
    x402_treasury_private_key: str = ""

    # Per-user custodial wallets. Each signed-in account gets its own generated
    # agent keypair (see services/wallet.ensure_user_wallet); the private key
    # is Fernet-encrypted with this symmetric key before it touches the
    # database. The backend holds the key — that is what makes these wallets
    # custodial. Generate with:
    #     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    #
    # Empty disables per-user provisioning: WalletService then falls back to the
    # single env wallet for everyone, i.e. exactly the pre-003 behaviour. So an
    # unset key degrades to the old single-tenant demo rather than crashing.
    wallet_encryption_key: str = ""
    # What the treasury sends a brand-new user wallet on first provision. XSGD
    # backs their card and settles their purchases; AVAX is gas so the wallet
    # can sign its own settlement. Small on purpose: every new account is a
    # treasury withdrawal, and the Fuji treasury's AVAX is the binding limit
    # (~0.05 each against ~1 AVAX = ~19 users before a faucet top-up).
    new_wallet_xsgd_amount: float = 100.0
    new_wallet_avax_gas: float = 0.05
    # The owner keeps the env wallet (X402_AGENT_*) rather than a generated one,
    # so the existing deployment and every test that runs outside a request
    # keep working unchanged. Flip to False only to force the owner onto a
    # provisioned wallet too.
    owner_uses_env_wallet: bool = True
    # Comma-separated extra networks to register the signer for, beyond
    # x402_network. See docs/HACKATHON_SWAP.md.
    x402_extra_networks: str = ""
    # Mainnet signing is OFF unless this is explicitly flipped. Bazaar listings
    # are third-party services on mainnets, so registering the signer there is
    # what makes a real-money payment possible — that has to be a deliberate
    # act, matching wallet._transfer's refusal to move mainnet funds from an
    # endpoint.
    x402_allow_mainnet: bool = False

    # Where agent-initiated purchases/replacements are executed. Points at our
    # own x402 vendor until real x402 merchants exist — swap when they do.
    action_vendor_url: str = "http://127.0.0.1:8000/vendor/deep-report"

    # Who receives the stablecoin when a card purchase settles on-chain. Empty
    # falls back to the treasury, which is the demo's merchant and is already
    # the x402 vendor's payTo. Named separately because "the wallet that funds
    # the agent" and "the wallet that gets paid" are different roles that only
    # happen to be the same address in a single-operator demo.
    merchant_settlement_address: str = ""

    # Avalanche Fuji RPC — the chain the funding and settlement legs run on.
    avalanche_fuji_rpc_url: str = "https://api.avax-test.network/ext/bc/C/rpc"

    # The token the Funding pillar reads and moves. Each network in
    # services/wallet.py carries a default; these override it without a code
    # change. An empty value means "use the network default".
    #
    # VERIFIED on-chain 2026-08-12 via WalletService.probe_token: XSGD is at
    # 0xb2F85b7AB3c2b6f62DF06dE6aE7D09c010a5096E on Avalanche C-Chain MAINNET,
    # symbol XSGD, 6 decimals. Still no public Fuji deployment — the testnet
    # stand-in in infra/contracts/XSGDTest.sol exists for that reason.
    #
    # STABLECOIN_DECIMALS is now a fallback rather than the source of truth:
    # WalletService reads decimals() off the contract and only falls back here
    # when the RPC is unreachable. Leave it blank unless you have a reason.
    stablecoin_contract: str = ""
    stablecoin_symbol: str = ""
    stablecoin_decimals: int = 0

    # Card issuance — "mock" (offline fake), "stripe" (Issuing test mode),
    # "straitsx" (event sandbox, adapter stubbed until then).
    card_issuer: str = "mock"
    stripe_api_key: str = ""
    stripe_cardholder_id: str = ""

    # StraitsX card issuing. Every unknown about their sandbox is an env var
    # rather than a code change, because the credentials and the exact API
    # shape only arrive at the event and 11pm on the Friday is the worst
    # possible time to be editing a client. See services/cards.py.
    straitsx_base_url: str = ""
    straitsx_api_key: str = ""
    # Auth scheme varies by provider: "Authorization: Bearer x" is the common
    # default, but plenty use "X-API-Key: x" with no prefix.
    straitsx_auth_header: str = "Authorization"
    straitsx_auth_prefix: str = "Bearer "
    # Endpoint paths. {id} is substituted with the issuer's card id.
    straitsx_cards_path: str = "/v1/cards"
    straitsx_card_path: str = "/v1/cards/{id}"
    straitsx_card_secrets_path: str = "/v1/cards/{id}/secrets"
    straitsx_card_txns_path: str = "/v1/cards/{id}/transactions"
    # How a card is killed after its single use — DELETE, or a status PATCH.
    straitsx_cancel_method: str = "POST"
    straitsx_cancel_path: str = "/v1/cards/{id}/terminate"
    # Card currency. Their product is XSGD/XUSD; the demo storefront prices in
    # USD, so which one funds a USD-priced card is a booth question.
    straitsx_currency: str = "USD"
    straitsx_limit_interval: str = "per_authorization"

    # Demo storefront (Shopify dev store) driven by Playwright.
    shop_store_url: str = ""
    shop_storefront_password: str = ""
    # "bogus" = Shopify Bogus Gateway magic PANs (pre-event); "real" = actual
    # issued-card PAN (event/StraitsX). Same checkout code path either way.
    checkout_gateway_profile: str = "bogus"
    # "chromium" = Playwright-bundled build; "chrome" = system Chrome (Smart
    # App Control fallback — bundled build verified working 2026-07-19).
    playwright_channel: str = "chromium"
    # Headed browser for stage demos (verify + checkout); headless for tests.
    browser_headed: bool = False

    # Demo shipping profile the checkout executor fills in (test orders only).
    # Defaults are US because a fresh dev store's only shipping zone is US —
    # a non-US address gets no shipping method and the checkout stalls.
    shipping_name: str = "Laeria Agent"
    # Must be a resolvable domain — Shopify checkout rejects invented TLDs.
    shipping_email: str = "laeria.agent.demo@gmail.com"
    shipping_address1: str = "123 Demo Street"
    shipping_city: str = "San Francisco"
    shipping_postal_code: str = "94111"
    shipping_country: str = "US"
    shipping_zone: str = "CA"
    # Shipping/taxes allowance on top of the verified product price when
    # sizing the card limit and checkout ceiling. The mandate caps still
    # bound the final number — this only stops shipping from tripping the
    # gate on an otherwise-approved purchase.
    checkout_shipping_buffer_usd: float = 20.0
    # How far the live price may drift above the amount a human approved
    # before the action is sent back for re-approval instead of executing.
    # Consent is for an amount, not merely for a purchase — this is what
    # makes the approved figure a real ceiling rather than a starting point.
    price_drift_tolerance: float = 0.05

    # App
    cors_origins: str = ""  # comma-separated; empty = allow all (dev)
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
