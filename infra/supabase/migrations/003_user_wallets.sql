-- 003: per-user custodial agent wallets.
--
-- Until now the agent wallet was a single global keypair in the backend env
-- (X402_AGENT_PRIVATE_KEY / _ADDRESS), which is why the payment routes were
-- owner-only: a second account acting on them would spend the owner's money.
--
-- These two columns give every user their own generated wallet instead. The
-- private key is Fernet-encrypted with WALLET_ENCRYPTION_KEY (backend env) and
-- stored here — custodial by design; the backend holds the key.
--
-- Both are nullable and additive: nothing reads them unless the per-user
-- wallet code is running, so applying this on a database serving the old
-- single-wallet code is a no-op. Safe to apply before the code ships.
--
-- Apply in the Supabase SQL editor, or via `supabase db push`.

alter table profiles add column if not exists wallet_address text;
alter table profiles add column if not exists wallet_key_encrypted text;
