-- 001: declare the profiles.mandate column that the backend already uses.
-- db/repositories.py get_mandate/set_mandate read/write profiles.mandate jsonb,
-- but the column was never in schema.sql. Existing databases created it ad hoc
-- (or writes silently failed on fresh installs) — this makes it official.
-- Apply in the Supabase SQL editor, or via `supabase db push`.

alter table profiles add column if not exists mandate jsonb;
