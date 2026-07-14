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

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "baryon.ai/0.1"
    reddit_username: str = ""
    reddit_password: str = ""
    # Optional egress proxy for Reddit requests. Datacenter IPs (Hetzner etc.)
    # are 403-blocked by Reddit; a residential/rotating proxy makes VPS-hosted
    # scraping work. Empty = direct connection (fine on a residential IP).
    # Format: "http://user:pass@host:port" or "socks5://host:port".
    reddit_proxy: str = ""

    # Obsidian
    obsidian_api_url: str = "https://127.0.0.1:27124"
    obsidian_api_key: str = ""
    obsidian_verify_ssl: bool = False

    # Owner (single-user mode until auth lands): UUID of the Supabase auth
    # user that owns all monitored items/alerts. Create one in the dashboard
    # (Authentication -> Users -> Add user) and paste its id here.
    owner_user_id: str = ""

    # x402 — real protocol on Base Sepolia testnet (free faucet USDC).
    # Flip network to "eip155:8453" (Base mainnet) + fund the agent wallet
    # with real USDC for production. Same code either way.
    x402_network: str = "eip155:84532"
    x402_facilitator_url: str = "https://x402.org/facilitator"
    x402_agent_private_key: str = ""
    x402_agent_address: str = ""
    x402_treasury_address: str = ""
    x402_treasury_private_key: str = ""

    # Where agent-initiated purchases/replacements are executed. Points at our
    # own x402 vendor until real x402 merchants exist — swap when they do.
    action_vendor_url: str = "http://127.0.0.1:8000/vendor/deep-report"

    # App
    cors_origins: str = ""  # comma-separated; empty = allow all (dev)
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
