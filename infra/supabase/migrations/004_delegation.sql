-- 004: signed mandate delegation credential.
--
-- The mandate is a set of spend rules; the delegation is the user's own wallet
-- signing those rules (EIP-712), so the agent's authority traces to a signature
-- the user made beforehand rather than a row we could have written ourselves.
-- This is the "Delegate" step of the payment lifecycle: issuer signs a
-- delegation credential (cap, expiry).
--
-- Shape: {signature, signed_by, chain_id, message, signed_at}. Nullable and
-- additive — nothing reads it unless the delegation code runs.
--
-- Apply in the Supabase SQL editor, or via `supabase db push`.

alter table profiles add column if not exists delegation jsonb;
