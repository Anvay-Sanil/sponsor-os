-- ============================================================================
-- Migration 004 — The Loop (Phase 5)
-- One paste in the SQL Editor: voidable outcomes, atomic logging RPC,
-- ranker audit table, pgvector similarity RPC.
-- ============================================================================

-- Voidable outcomes (mis-tap undo). Never delete — auditability + clean labels.
alter table public.outcomes
  add column if not exists voided    boolean not null default false,
  add column if not exists voided_by uuid references public.profiles (id),
  add column if not exists voided_at timestamptz;

-- Ranker audit: one row per training run (the model-card pattern again).
create table public.ranker_runs (
  id                 bigint generated always as identity primary key,
  model_version      text not null unique,
  trained_at         timestamptz not null default now(),
  n_outcomes         integer not null,
  cv_auc             double precision,
  feature_importance jsonb not null default '{}'::jsonb,
  model_card         text,           -- MUST include the selection-bias paragraph
  status             text not null default 'active'
                     check (status in ('active', 'retired'))
);

alter table public.ranker_runs enable row level security;
create policy ranker_runs_select on public.ranker_runs
  for select to authenticated using (true);
-- writes: training notebook via service key only; no client policy.

-- Shared event -> lead-status mapping (used by both RPCs below).
create or replace function public.event_to_status(p_event public.outcome_event)
returns public.lead_status
language sql immutable
as $$
  select case p_event
    when 'sent'     then 'contacted'::public.lead_status
    when 'replied'  then 'replied'::public.lead_status
    when 'meeting'  then 'meeting'::public.lead_status
    when 'signed'   then 'signed'::public.lead_status
    when 'ghosted'  then 'ghosted'::public.lead_status
    when 'rejected' then 'rejected'::public.lead_status
  end;
$$;

-- ATOMIC one-tap logging: outcomes insert + leads.status update in ONE
-- transaction, so flaky hostel Wi-Fi can never leave the two tables disagreeing.
create or replace function public.log_outcome(
  p_lead_id    bigint,
  p_event      public.outcome_event,
  p_deal_value numeric default null,
  p_notes      text default null
)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
  v_id bigint;
begin
  if public.get_my_role() not in ('sponsorship', 'admin') then
    raise exception 'NOT_ALLOWED';
  end if;
  if not exists (select 1 from leads where id = p_lead_id) then
    raise exception 'LEAD_NOT_FOUND';
  end if;
  insert into outcomes (lead_id, event, deal_value, notes, logged_by)
  values (p_lead_id, p_event, p_deal_value, nullif(trim(p_notes), ''), auth.uid())
  returning id into v_id;
  update leads set status = public.event_to_status(p_event) where id = p_lead_id;
  return v_id;
end;
$$;

revoke execute on function public.log_outcome(bigint, public.outcome_event, numeric, text) from public;
grant execute on function public.log_outcome(bigint, public.outcome_event, numeric, text) to authenticated;

-- Undo: logger may void their OWN outcome within 10 minutes; admin anytime.
-- Rolls the lead's status back to the latest surviving outcome (or 'new').
create or replace function public.void_outcome(p_outcome_id bigint)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row    outcomes%rowtype;
  v_role   public.user_role;
  v_event  public.outcome_event;
begin
  v_role := public.get_my_role();
  select * into v_row from outcomes where id = p_outcome_id for update;
  if not found then
    raise exception 'NOT_FOUND';
  end if;
  if v_row.voided then
    return;  -- idempotent
  end if;
  if v_role <> 'admin'
     and not (v_row.logged_by = auth.uid()
              and v_row.logged_at > now() - interval '10 minutes') then
    raise exception 'VOID_WINDOW_CLOSED';
  end if;
  update outcomes
     set voided = true, voided_by = auth.uid(), voided_at = now()
   where id = p_outcome_id;
  select event into v_event
    from outcomes
   where lead_id = v_row.lead_id and voided = false
   order by logged_at desc limit 1;
  if found then
    update leads set status = public.event_to_status(v_event) where id = v_row.lead_id;
  else
    update leads set status = 'new' where id = v_row.lead_id;
  end if;
end;
$$;

revoke execute on function public.void_outcome(bigint) from public;
grant execute on function public.void_outcome(bigint) to authenticated;

-- Sponsorship members write pitch_memory rows when logging positive outcomes
-- (Phase 1 left it admin-only; the loop needs the people who log to write it).
create policy pitch_memory_insert_sponsorship on public.pitch_memory
  for insert to authenticated
  with check (public.get_my_role() in ('sponsorship', 'admin'));

-- pgvector similarity: winning pitch language for new decks. Real-lead decks
-- call with p_include_test=false so demo practice never leaks into real pitches.
create or replace function public.match_pitch_memory(
  p_query        vector(768),
  p_count        integer default 3,
  p_include_test boolean default false
)
returns table (id bigint, snippet text, outcome_label text, similarity double precision)
language sql
stable
as $$
  select pm.id, pm.text as snippet, pm.outcome_label,
         1 - (pm.embedding <=> p_query) as similarity
  from pitch_memory pm
  where pm.embedding is not null
    and (p_include_test or pm.outcome_label not like 'test\_%')
  order by pm.embedding <=> p_query
  limit greatest(p_count, 1);
$$;

revoke execute on function public.match_pitch_memory(vector, integer, boolean) from public;
grant execute on function public.match_pitch_memory(vector, integer, boolean) to authenticated;
