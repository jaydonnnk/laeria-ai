# Build Guide

Phased plan. Each phase is independently useful — you can stop at any phase and have something that works. Do not start a phase until the previous one's checkpoint passes.

## Phase 0 — Foundation
- **0.1 Repo structure** — this scaffold.
- **0.2 Supabase schema** — apply `infra/supabase/schema.sql`.
- **0.3 FastAPI skeleton** — routes wired, returning 501. Done in this scaffold.
- **0.4 Environment validation** — `python -m tests.test_environment` must pass all hard checks (Reddit, OpenRouter, Supabase) before Phase 1. Obsidian is a soft check.

## Phase 1 — Mode 2: Decision Synthesis (build first)
User types a decision/purchase query → agent reads Reddit across subreddits → structured consensus brief. Synchronous, no persistence required to start.
- Implement `RedditService`: `find_relevant_subreddits`, `search_subreddit`, `get_thread_with_comments`, `apply_signal_filters`.
- Implement `LLMService.complete_json`.
- Implement `ResearchAgent.synthesise_decision`.
- Wire `POST /research/decision`.
- Frontend: `/decision` page with the brief card.
- **Checkpoint:** run against 5 queries you can personally verify. If synthesis is shallow or wrong, fix the prompt + filters before Phase 2.

## Phase 2 — Mode 1: Retrospective Mining
Reuses Phase 1 infra; adds retrospective search strategy + outcomes synthesis + thin-coverage fallback.
- Implement `RedditService.search_retrospective`.
- Implement `ResearchAgent.mine_retrospectives` (falls back to low-confidence when <5 retrospective posts).
- Wire `POST /research/retrospective`.
- Frontend: `/research` page showing retrospective count prominently.

## Phase 3 — Mode 3: Monitoring (hardest phase)
Persistent VPS worker + alert engine + Obsidian integration.
- Implement `ObsidianService` (read vault → infer items; write alerts/logs).
- Implement `AlertEngine.evaluate` (alert on change vs baseline, not absolute sentiment).
- Implement `MonitorWorker.run_cycle`.
- Deploy `infra/systemd/reddit-signal-monitor.service` on the VPS.
- Frontend: `/monitor` dashboard.
- Poll every 4–6h per item. Never continuous.

## Phase 4 — Payments (x402 + AP2)
- Implement `PaymentService.verify_within_mandate` (this is the real logic).
- x402 flow against a demo endpoint on the VPS (real vendors aren't x402-enabled).
- Confirmation flow for actions above the mandate threshold.

## Phase 5 — Polish
- PRAW rate-limit backoff, WARP poisoning defenses, pgvector dedup, bidirectional Obsidian sync, usage dashboard.

## Honest risk
Phase 3 is more complex than Phases 1+2 combined. Phase 1 alone is a complete, useful product. Ship it, use it, then decide if Phase 3 is worth building.
