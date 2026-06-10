-- ============================================================================
-- Migration 003 — Pitch v1 (Phase 3)
-- One paste in the SQL Editor: creates the private `decks` Storage bucket and
-- its access policies. No dashboard clicking needed.
-- ============================================================================

-- Private bucket for generated PPTX decks (paths: {lead_id}/deck_v{n}.pptx).
insert into storage.buckets (id, name, public)
values ('decks', 'decks', false)
on conflict (id) do nothing;

-- Upload: sponsorship + admin only (the roles that may generate decks).
create policy decks_storage_upload on storage.objects
  for insert to authenticated
  with check (bucket_id = 'decks' and public.get_my_role() in ('sponsorship', 'admin'));

-- Read/download: any logged-in member.
create policy decks_storage_read on storage.objects
  for select to authenticated
  using (bucket_id = 'decks');

-- Delete: admin only (destructive actions stay admin).
create policy decks_storage_delete on storage.objects
  for delete to authenticated
  using (bucket_id = 'decks' and public.get_my_role() = 'admin');
