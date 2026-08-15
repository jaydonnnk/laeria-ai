-- 005: per-user shipping profile.
--
-- The agent checks out physical goods on the card rail (services/checkout.py
-- execute_checkout), which needs a name, contact email, and a shipping address.
-- Until now those came from a single set of SHIPPING_* env vars — one address
-- for the whole deployment. This moves them per-user: each signed-in account
-- carries the details the agent ships to, and the env vars stay as the fallback
-- for an account that has not filled its profile in yet.
--
-- Shape: {name, email, address1, city, postal_code, country_code, zone_code}.
-- Nullable and additive — nothing reads it unless a profile has been saved;
-- an empty column falls back to the env shipping profile.
--
-- Apply in the Supabase SQL editor, or via `supabase db push`.

alter table profiles add column if not exists shipping jsonb;
