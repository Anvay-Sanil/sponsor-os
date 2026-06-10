-- ============================================================================
-- Migration 002 — Scout v1 (Phase 2)
-- For projects that already applied schema.sql (Phase 1):
-- paste this whole file in the SQL Editor and Run. Fresh installs can skip
-- this file — supabase/schema.sql now includes everything below.
-- ============================================================================

-- Regional affinity pass-through: evidence collected from a same-region
-- (Jaipur/Rajasthan) source is a stronger signal for an MUJ chapter event.
-- Stored now, used by scoring in a later version.
alter table public.evidence
  add column if not exists region_match boolean not null default false;

-- Rival-fest seed list lives in the DB (admins edit it in the UI; the
-- Streamlit host filesystem is ephemeral so a YAML file cannot be the
-- source of truth). jobs/seeds/rival_fests.yaml is only the one-time bootstrap.
create table public.scout_seeds (
  id           bigint generated always as identity primary key,
  name         text not null,
  url          text not null unique,
  region_match boolean not null default false,  -- Jaipur/Rajasthan source?
  enabled      boolean not null default true,
  notes        text,
  added_by     uuid references public.profiles (id) on delete set null,
  created_at   timestamptz not null default now()
);

-- One row per Scout execution; the Admin page shows the latest.
create table public.scout_runs (
  id          bigint generated always as identity primary key,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  status      text not null default 'running'
              check (status in ('running', 'success', 'partial', 'failed')),
  stats       jsonb not null default '{}'::jsonb,
  log         text
);

alter table public.scout_seeds enable row level security;
alter table public.scout_runs  enable row level security;

create policy scout_seeds_select on public.scout_seeds
  for select to authenticated using (true);
create policy scout_seeds_admin on public.scout_seeds
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

create policy scout_runs_select on public.scout_runs
  for select to authenticated using (true);
-- scout_runs writes: service-role key only (bypasses RLS); no client policy.
