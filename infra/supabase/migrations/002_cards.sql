-- 002: disposable virtual cards (hackathon Issuance pillar).
-- One row per issued card. Deliberately NO pan/cvc columns — credentials are
-- live-fetched from the issuer by services/cards.py get_details and never
-- stored. Apply in the Supabase SQL editor, or via `supabase db push`.

create table if not exists cards (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    action_id uuid references actions (id) on delete set null,
    issuer text not null,                        -- mock | stripe | straitsx
    issuer_card_id text not null,
    last4 text not null,
    exp_month int not null,
    exp_year int not null,
    spend_limit_usd numeric not null default 0,
    status text not null default 'active'
        check (status in ('issued', 'active', 'canceled')),
    metadata jsonb not null default '{}',
    created_at timestamptz not null default now(),
    canceled_at timestamptz
);

create index if not exists cards_user_created_idx on cards (user_id, created_at desc);

alter table cards enable row level security;

create policy "own_cards" on cards
    for all using (auth.uid() = user_id);
