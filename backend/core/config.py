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
    openrouter_model: str = "anthropic/claude-sonnet-4.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Per-request ceiling. The OpenAI SDK defaults to 600s, which turns an
    # intermittent stall into a ten-minute hang indistinguishable from slow
    # work. Synthesis normally lands in 15-40s.
    llm_timeout_seconds: int = 120

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

    # x402 — real protocol on Base Sepolia testnet (free faucet USDC).
    # Flip network to "eip155:8453" (Base mainnet) + fund the agent wallet
    # with real USDC for production. Same code either way.
    x402_network: str = "eip155:84532"
    x402_facilitator_url: str = "https://x402.org/facilitator"
    x402_agent_private_key: str = ""
    x402_agent_address: str = ""
    x402_treasury_address: str = ""
    x402_treasury_private_key: str = ""
    # Comma-separated extra networks to register the signer for, beyond
    # x402_network — e.g. "eip155:43113" for Avalanche Fuji.
    # See docs/HACKATHON_SWAP.md.
    x402_extra_networks: str = ""
    # Base mainnet signing is OFF unless this is explicitly flipped. Bazaar
    # listings are mainnet, so registering the signer there is what makes a
    # real-money payment possible — that has to be a deliberate act, matching
    # wallet.fund_agent's refusal to move mainnet funds from an endpoint.
    x402_allow_mainnet: bool = False

    # Where agent-initiated purchases/replacements are executed. Points at our
    # own x402 vendor until real x402 merchants exist — swap when they do.
    action_vendor_url: str = "http://127.0.0.1:8000/vendor/deep-report"

    # Avalanche Fuji testnet (hackathon rail swap; see docs/HACKATHON_SWAP.md)
    avalanche_fuji_rpc_url: str = "https://api.avax-test.network/ext/bc/C/rpc"

    # Card issuance — "mock" (offline fake), "stripe" (Issuing test mode),
    # "straitsx" (event sandbox, adapter stubbed until then).
    card_issuer: str = "mock"
    stripe_api_key: str = ""
    stripe_cardholder_id: str = ""

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
