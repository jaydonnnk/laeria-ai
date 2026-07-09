# reddit-signal

An agentic research and monitoring tool that treats Reddit as a human-truth signal source across the full lifecycle of a decision.

Three modes:

1. **Decision synthesis** (Mode 2) — deep multi-subreddit research before you buy or decide, returning a structured consensus brief.
2. **Retrospective mining** (Mode 1) — finds update/outcome posts from people who already made the same decision.
3. **Monitoring** (Mode 3) — watches subreddits for signal about things you already own or subscribe to, and alerts (or acts) when something changes.

This repository is the Phase 0.1 scaffold: structure, config, and skeletons only. No feature logic is implemented yet. See `docs/BUILD_GUIDE.md` for the phased plan.

## Stack

| Layer         | Choice                                             |
|---------------|----------------------------------------------------|
| Frontend      | Next.js 14 (App Router) — Vercel                   |
| Backend API   | FastAPI — Hetzner VPS                               |
| Agent layer   | Python — same VPS, systemd services                |
| Database      | Supabase (Postgres + pgvector + Auth + RLS)        |
| LLM           | OpenRouter                                          |
| Reddit        | PRAW (own app credentials)                          |
| Obsidian      | Local REST API plugin (localhost:27124)            |
| Payments      | x402 + AP2 (Phase 4, not yet implemented)          |

## Repository layout

```
frontend/          Next.js app
backend/
  api/             FastAPI route handlers
  agents/          Agent logic (research, alert engine)
  services/        External clients (Reddit, Obsidian, LLM, payment)
  workers/         Background jobs (monitor worker)
  core/            Config, logging, shared internals
  db/              Supabase client, schema helpers
  tests/           Environment + unit tests
shared/            Cross-language type definitions
infra/
  systemd/         Unit files for VPS workers
  supabase/        SQL schema and migrations
docs/              Build guide and architecture notes
```

## Getting started

This is a scaffold. Nothing runs end-to-end yet. To validate the environment (Phase 0.4):

```bash
cd backend
cp .env.example .env      # fill in credentials
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m tests.test_environment
```

Do not build Phase 1 features until all five environment checks pass.

## Status

- [x] Phase 0.1 — Repo structure (this scaffold)
- [ ] Phase 0.2 — Supabase schema
- [ ] Phase 0.3 — FastAPI skeleton wired
- [ ] Phase 0.4 — Environment validation passing
- [ ] Phase 1 — Mode 2 decision synthesis
- [ ] Phase 2 — Mode 1 retrospective mining
- [ ] Phase 3 — Mode 3 monitoring
- [ ] Phase 4 — Payments
- [ ] Phase 5 — Polish and hardening
