-- baryon.ai database schema (Phase 0.2)
-- Apply in the Supabase SQL editor, or via `supabase db push`.
-- Mirrors backend/core/models.py — keep the two in sync manually.

-- ---- Extensions ----
create extension if not exists vector;      -- pgvector for thread embeddings (Phase 5)

-- ---- Profiles (extends Supabase auth.users) ----
create table if not exists profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    obsidian_configured boolean not null default false,
    reddit_configured boolean not null default false,
    created_at timestamptz not null default now()
);

-- ---- Research sessions (Modes 1 & 2) ----
create table if not exists research_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    query text not null,
    mode text not null check (mode in ('decision_synthesis', 'retrospective')),
    status text not null default 'pending'
        check (status in ('pending', 'running', 'complete', 'failed')),
    created_at timestamptz not null default now()
);

create table if not exists research_results (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references research_sessions (id) on delete cascade,
    subreddits_searched text[] not null default '{}',
    threads_read int not null default 0,
    brief_json jsonb,                 -- ResearchBrief (Mode 2)
    confidence_level text,            -- high | moderate | low
    created_at timestamptz not null default now()
);

-- Mode 1 detail (extends a research_session)
create table if not exists decision_queries (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references research_sessions (id) on delete cascade,
    decision_text text not null,
    context_text text default '',
    retrospective_count int not null default 0,
    outcome_summary jsonb,            -- OutcomeSummary
    created_at timestamptz not null default now()
);

-- ---- Monitoring (Mode 3) ----
create table if not exists monitored_items (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    name text not null,
    category text default '',
    subreddits text[] not null default '{}',
    check_interval_hours int not null default 6,
    action_mandate jsonb not null default '{}'::jsonb,   -- ActionMandate
    active boolean not null default true,
    last_checked_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists monitor_runs (
    id uuid primary key default gen_random_uuid(),
    item_id uuid not null references monitored_items (id) on delete cascade,
    ran_at timestamptz not null default now(),
    posts_found int not null default 0,
    sentiment text,
    signal_level text default 'none'
        check (signal_level in ('none', 'low', 'medium', 'high')),
    raw_findings jsonb
);

create table if not exists monitor_alerts (
    id uuid primary key default gen_random_uuid(),
    item_id uuid not null references monitored_items (id) on delete cascade,
    run_id uuid references monitor_runs (id) on delete set null,
    severity text not null
        check (severity in ('none', 'low', 'medium', 'high')),
    summary text not null,
    thread_urls text[] not null default '{}',
    recommended_action text default 'none',
    actioned boolean not null default false,
    created_at timestamptz not null default now()
);

-- ---- Actions (Phase 4) ----
create table if not exists actions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    type text not null,
    target text not null,
    status text not null default 'pending_approval',
    amount_usd numeric(10, 2) not null default 0,
    mandate_ref text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

-- ---- Thread embeddings (Phase 5 dedup) ----
create table if not exists thread_embeddings (
    id uuid primary key default gen_random_uuid(),
    thread_id text not null,
    subreddit text not null,
    embedding vector(1536),
    created_at timestamptz not null default now()
);

-- ---- Indexes ----
create index if not exists idx_research_sessions_user on research_sessions (user_id);
create index if not exists idx_monitored_items_user on monitored_items (user_id);
create index if not exists idx_monitored_items_active on monitored_items (active);
create index if not exists idx_monitor_alerts_item on monitor_alerts (item_id);
create index if not exists idx_actions_user on actions (user_id);

-- ---- Row Level Security ----
-- Backend uses the service-role key (bypasses RLS), so query scoping by
-- user_id is enforced in application code. These policies protect any direct
-- client (anon key) access from the frontend.

alter table profiles enable row level security;
alter table research_sessions enable row level security;
alter table research_results enable row level security;
alter table decision_queries enable row level security;
alter table monitored_items enable row level security;
alter table monitor_runs enable row level security;
alter table monitor_alerts enable row level security;
alter table actions enable row level security;

-- Owner-only policies (user can see only their own rows).
create policy "own_profile" on profiles
    for all using (auth.uid() = id);

create policy "own_research_sessions" on research_sessions
    for all using (auth.uid() = user_id);

create policy "own_monitored_items" on monitored_items
    for all using (auth.uid() = user_id);

create policy "own_actions" on actions
    for all using (auth.uid() = user_id);

-- Child tables inherit access via their parent's user_id.
create policy "own_research_results" on research_results
    for all using (
        exists (
            select 1 from research_sessions s
            where s.id = research_results.session_id and s.user_id = auth.uid()
        )
    );

create policy "own_decision_queries" on decision_queries
    for all using (
        exists (
            select 1 from research_sessions s
            where s.id = decision_queries.session_id and s.user_id = auth.uid()
        )
    );

create policy "own_monitor_runs" on monitor_runs
    for all using (
        exists (
            select 1 from monitored_items i
            where i.id = monitor_runs.item_id and i.user_id = auth.uid()
        )
    );

create policy "own_monitor_alerts" on monitor_alerts
    for all using (
        exists (
            select 1 from monitored_items i
            where i.id = monitor_alerts.item_id and i.user_id = auth.uid()
        )
    );
