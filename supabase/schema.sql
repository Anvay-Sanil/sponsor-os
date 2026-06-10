-- ============================================================================
-- Sponsor OS — full database schema (Phase 1)
-- Apply once: Supabase Dashboard → SQL Editor → paste this whole file → Run.
--
-- After applying, also do these TWO dashboard settings (documented in README):
--   1. Authentication → Sign In / Up → Email → turn OFF "Confirm email"
--      (invite-code gating replaces email verification; juniors sign up
--       instantly on their phones).
--   2. (Phase 3) Storage → create a private bucket named `decks`.
--
-- Design notes:
--   * RLS is enabled on every table. The Streamlit app uses ONLY the anon key;
--     these policies are the real permission system.
--   * `public.get_my_role()` is SECURITY DEFINER to avoid recursive-RLS when
--     policies on `profiles` need to read `profiles`.
--   * `invite_codes` has NO general policies — the two RPCs below are the only
--     access path, so codes cannot be enumerated by any client.
--   * jobs/ scripts use the service-role key, which bypasses RLS entirely.
--   * Demo kill switch: brands.is_demo / leads.is_demo. Purge before production:
--       delete from public.leads  where is_demo;
--       delete from public.brands where is_demo;
-- ============================================================================

create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
create type public.user_role as enum ('admin', 'sponsorship', 'analyst', 'viewer');

create type public.lead_status as enum
  ('new', 'contacted', 'replied', 'meeting', 'signed', 'ghosted', 'rejected');

create type public.evidence_source as enum
  ('rival_fest_site', 'news', 'instagram', 'poster_logo');

-- NOTE: deviates from the original brief's (sent, opened_meeting, ...) — the
-- one-tap UI logs Sent / Reply / Meeting / Signed / Ghosted, so `opened_meeting`
-- is split into its two real events. Approved 2026-06-10.
create type public.outcome_event as enum
  ('sent', 'replied', 'meeting', 'signed', 'ghosted', 'rejected');

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create table public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  name       text not null,
  role       public.user_role not null default 'viewer',
  committee  text,
  created_at timestamptz not null default now()
);

create table public.invite_codes (
  code       text primary key,
  role       public.user_role not null,
  max_uses   integer not null default 1 check (max_uses > 0),
  uses       integer not null default 0 check (uses >= 0),
  expires_at timestamptz,
  created_at timestamptz not null default now()
);

create table public.brands (
  id              bigint generated always as identity primary key,
  name            text not null,
  normalized_name text not null unique,
  website         text,
  industry        text,
  region          text,
  palette_json    jsonb,
  is_demo         boolean not null default false,  -- demo kill switch
  first_seen      timestamptz not null default now(),
  last_enriched   timestamptz
);

create table public.evidence (
  id           bigint generated always as identity primary key,
  brand_id     bigint not null references public.brands (id) on delete cascade,
  source_url   text not null,
  source_type  public.evidence_source not null,
  snippet      text,
  confidence   double precision check (confidence between 0 and 1),
  region_match boolean not null default false,     -- same-region (Jaipur) source
  detected_at  timestamptz not null default now(),
  unique (brand_id, source_url)                    -- idempotent Scout upserts
);

-- Rival-fest seed list (admin-editable in the UI; YAML is bootstrap only).
create table public.scout_seeds (
  id           bigint generated always as identity primary key,
  name         text not null,
  url          text not null unique,
  region_match boolean not null default false,
  enabled      boolean not null default true,
  notes        text,
  added_by     uuid references public.profiles (id) on delete set null,
  created_at   timestamptz not null default now()
);

-- One row per Scout execution; Admin page shows the latest.
create table public.scout_runs (
  id          bigint generated always as identity primary key,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  status      text not null default 'running'
              check (status in ('running', 'success', 'partial', 'failed')),
  stats       jsonb not null default '{}'::jsonb,
  log         text
);

create table public.leads (
  id             bigint generated always as identity primary key,
  brand_id       bigint not null references public.brands (id) on delete cascade,
  fest_target    text not null default '',
  evidence_score double precision not null default 0,  -- 0–100 scale
  ml_score       double precision,                     -- null until ≥50 outcomes
  status         public.lead_status not null default 'new',
  owner_id       uuid references public.profiles (id) on delete set null,
  priority       text not null default 'medium'
                 check (priority in ('high', 'medium', 'low')),
  is_demo        boolean not null default false,       -- demo kill switch
  updated_at     timestamptz not null default now(),
  unique (brand_id, fest_target)                       -- idempotent Scout upserts
);

create table public.tiers (
  id              bigint generated always as identity primary key,
  name            text not null unique,
  components_json jsonb not null default '{}'::jsonb,
  base_price      numeric,
  last_priced_at  timestamptz
);

create table public.pricing_posteriors (
  id                bigint generated always as identity primary key,
  model_version     text not null,
  asset_type        text not null,
  posterior_samples jsonb not null,
  fitted_at         timestamptz not null default now(),
  unique (model_version, asset_type)
);

create table public.decks (
  id                bigint generated always as identity primary key,
  lead_id           bigint not null references public.leads (id) on delete cascade,
  generated_by      uuid references public.profiles (id) on delete set null,
  narrative_json    jsonb,
  pptx_storage_path text,        -- path inside the `decks` Storage bucket
  email_text        text,
  created_at        timestamptz not null default now()
);

create table public.outcomes (
  id         bigint generated always as identity primary key,
  lead_id    bigint not null references public.leads (id) on delete cascade,
  event      public.outcome_event not null,
  deal_value numeric,
  notes      text,
  logged_by  uuid references public.profiles (id) on delete set null,
  logged_at  timestamptz not null default now()
);

create table public.pitch_memory (
  id             bigint generated always as identity primary key,
  text           text not null,
  embedding      vector(768),   -- Gemini text-embedding-004 (NOT 384 / local models)
  outcome_label  text,
  source_deck_id bigint references public.decks (id) on delete set null,
  created_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger leads_touch_updated_at
  before update on public.leads
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Role helper (SECURITY DEFINER breaks the profiles→profiles RLS recursion)
-- ---------------------------------------------------------------------------
create or replace function public.get_my_role()
returns public.user_role
language sql
security definer
set search_path = public
stable
as $$
  select role from public.profiles where id = auth.uid();
$$;

revoke execute on function public.get_my_role() from public;
grant execute on function public.get_my_role() to authenticated;

-- ---------------------------------------------------------------------------
-- Invite-code RPCs — the ONLY access path to invite_codes for clients
-- ---------------------------------------------------------------------------

-- Pre-signup check: returns the role for a valid code, else null.
-- Requires the full exact code, so it cannot be used to enumerate codes.
create or replace function public.check_invite(p_code text)
returns text
language sql
security definer
set search_path = public
stable
as $$
  select role::text
  from invite_codes
  where code = p_code
    and uses < max_uses
    and (expires_at is null or expires_at > now());
$$;

revoke execute on function public.check_invite(text) from public;
grant execute on function public.check_invite(text) to anon, authenticated;

-- Post-signup redemption: atomically validates the code, creates the caller's
-- profile with the mapped role, and increments uses — one transaction, so no
-- orphan profiles and no race past max_uses. Idempotent if a profile exists.
create or replace function public.redeem_invite(
  p_code      text,
  p_name      text default null,
  p_committee text default null
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_role public.user_role;
begin
  if auth.uid() is null then
    raise exception 'not authenticated';
  end if;

  -- Already redeemed? Return the existing role (makes retries safe).
  select role into v_role from profiles where id = auth.uid();
  if found then
    return v_role::text;
  end if;

  select role into v_role
  from invite_codes
  where code = p_code
    and uses < max_uses
    and (expires_at is null or expires_at > now())
  for update;                       -- lock the row: atomic uses increment

  if not found then
    raise exception 'INVITE_INVALID';
  end if;

  update invite_codes set uses = uses + 1 where code = p_code;

  insert into profiles (id, name, role, committee)
  values (
    auth.uid(),
    coalesce(nullif(trim(p_name), ''), split_part(coalesce(auth.email(), 'member'), '@', 1)),
    v_role,
    p_committee
  );

  return v_role::text;
end;
$$;

revoke execute on function public.redeem_invite(text, text, text) from public;
grant execute on function public.redeem_invite(text, text, text) to authenticated;

-- ---------------------------------------------------------------------------
-- Row Level Security
-- Matrix: viewer/analyst SELECT-only; sponsorship INSERT/UPDATE on
-- leads/decks/outcomes (no DELETE); admin full. Outcome corrections
-- (UPDATE outcomes) are admin-only.
-- ---------------------------------------------------------------------------
alter table public.scout_seeds        enable row level security;
alter table public.scout_runs         enable row level security;
alter table public.profiles           enable row level security;
alter table public.invite_codes       enable row level security;
alter table public.brands             enable row level security;
alter table public.evidence           enable row level security;
alter table public.leads              enable row level security;
alter table public.tiers              enable row level security;
alter table public.pricing_posteriors enable row level security;
alter table public.decks              enable row level security;
alter table public.outcomes           enable row level security;
alter table public.pitch_memory       enable row level security;

-- profiles: everyone logged in can see names (Lead Board owner column);
-- users may update their own row but NOT their role; admin full.
create policy profiles_select on public.profiles
  for select to authenticated using (true);

create policy profiles_update_own on public.profiles
  for update to authenticated
  using (id = auth.uid())
  with check (id = auth.uid() and role = public.get_my_role());

create policy profiles_admin_all on public.profiles
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

-- invite_codes: admin only; everyone else goes through the RPCs above.
create policy invite_codes_admin on public.invite_codes
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

-- brands / evidence / tiers / pricing_posteriors / pitch_memory:
-- readable by all roles; written only by admin (app) or service key (jobs).
create policy brands_select on public.brands
  for select to authenticated using (true);
create policy brands_admin on public.brands
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

create policy evidence_select on public.evidence
  for select to authenticated using (true);
create policy evidence_admin on public.evidence
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

create policy tiers_select on public.tiers
  for select to authenticated using (true);
create policy tiers_admin on public.tiers
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

create policy posteriors_select on public.pricing_posteriors
  for select to authenticated using (true);
create policy posteriors_admin on public.pricing_posteriors
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

create policy pitch_memory_select on public.pitch_memory
  for select to authenticated using (true);
create policy pitch_memory_admin on public.pitch_memory
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

create policy scout_seeds_select on public.scout_seeds
  for select to authenticated using (true);
create policy scout_seeds_admin on public.scout_seeds
  for all to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');

create policy scout_runs_select on public.scout_runs
  for select to authenticated using (true);
-- scout_runs writes: service-role key only (bypasses RLS); no client policy.

-- leads: all read; sponsorship+admin write; only admin deletes.
create policy leads_select on public.leads
  for select to authenticated using (true);
create policy leads_insert on public.leads
  for insert to authenticated
  with check (public.get_my_role() in ('sponsorship', 'admin'));
create policy leads_update on public.leads
  for update to authenticated
  using (public.get_my_role() in ('sponsorship', 'admin'))
  with check (public.get_my_role() in ('sponsorship', 'admin'));
create policy leads_delete_admin on public.leads
  for delete to authenticated
  using (public.get_my_role() = 'admin');

-- decks: all read; sponsorship+admin create; only admin deletes.
create policy decks_select on public.decks
  for select to authenticated using (true);
create policy decks_insert on public.decks
  for insert to authenticated
  with check (public.get_my_role() in ('sponsorship', 'admin'));
create policy decks_delete_admin on public.decks
  for delete to authenticated
  using (public.get_my_role() = 'admin');

-- outcomes: all read; sponsorship+admin log; corrections (update) admin-only.
create policy outcomes_select on public.outcomes
  for select to authenticated using (true);
create policy outcomes_insert on public.outcomes
  for insert to authenticated
  with check (public.get_my_role() in ('sponsorship', 'admin'));
create policy outcomes_update_admin on public.outcomes
  for update to authenticated
  using (public.get_my_role() = 'admin')
  with check (public.get_my_role() = 'admin');
create policy outcomes_delete_admin on public.outcomes
  for delete to authenticated
  using (public.get_my_role() = 'admin');
