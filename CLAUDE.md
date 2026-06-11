# Sponsor OS — Project Instructions

## MISSION

Sponsor OS is an autonomous sponsorship pipeline for the ACM SIGAI student chapter at
Manipal University Jaipur. It is a three-stage closed-loop system:

1. **SCOUT** — batch agents find and rank sponsorship leads from public evidence
   (rival fest websites, news, public social posts).
2. **PRICE** — a Bayesian model converts the chapter's event inventory (stage logos,
   reels, booths) into priced tiers with confidence intervals, per lead.
3. **PITCH** — an agent compiles a bespoke PPTX deck + cold email per lead, using
   Scout's evidence and Price's numbers.

A feedback loop logs outreach outcomes (replied / meeting / signed / ghosted) back into
the database to improve ranking, pricing priors, and pitch language over time.

The system is used daily by non-technical junior committee members. UI simplicity is a
hard requirement, not a nice-to-have.

## NON-NEGOTIABLE CONSTRAINTS

1. **₹0 operating cost. Forever.** Every component runs on free tiers: Groq free tier
   (Llama 3.3 70B), Gemini API free tier, Supabase free tier, Streamlit Community Cloud,
   GitHub Actions free minutes, Google Colab for heavy batch jobs. If a design decision
   would require a paid plan at chapter scale (~15 users, ~500 leads, ~30 decks/month),
   reject it and choose the free alternative.
2. **Nothing always-on except the Streamlit app.** All heavy work (scraping, CV, model
   fitting) runs as batch jobs: GitHub Actions cron or manually-triggered Colab
   notebooks. No background workers, no queues, no paid daemons.
3. **Multi-user from day one.** Shared persistent state lives in Supabase Postgres —
   never local SQLite, never files on the Streamlit host (its filesystem is ephemeral
   and wipes on redeploy).
4. **Role-based access via Supabase Auth:**
   - `admin` — full access, user management, destructive actions, outcome corrections
   - `sponsorship` — view/edit leads, generate decks, log outcomes, run tier simulator
   - `analyst` — read-only on leads + full access to Price simulator and dashboards
   - `viewer` — read-only dashboards (for faculty advisors / chapter leads)
   Signup is by invite code only (an `invite_codes` table mapping code → role).
   No open registration.
5. **Public-data scraping only.** Public webpages and public posts. Respect robots.txt.
   Never scrape behind a login. Never store personal data of private individuals —
   brands, companies, and public event pages only. Instagram scraping (Instaloader) is
   brittle: optional enrichment behind a feature flag, with rival-fest websites and news
   as the primary evidence source. Wrap all scrapers in graceful failure — a broken
   scraper must never crash the pipeline, only log and skip.
6. **Honest AI.** Until ≥50 real outcomes are logged, the lead ranker runs on
   transparent heuristics — label it "Evidence Score," not "AI prediction," in the UI.
   Price's confidence intervals are always displayed; never show a point estimate alone.
   Every Scout score must link to its supporting evidence.
7. **Secrets** (GROQ_API_KEY, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY,
   SUPABASE_SERVICE_KEY) live in Streamlit secrets / GitHub Actions secrets / `.env`
   (gitignored). Never hardcode. Ship a `.env.example`.

## TECH STACK (FIXED — DO NOT SUBSTITUTE)

- Language: Python 3.11+
- UI: Streamlit (Streamlit Community Cloud), multipage app
- Database + Auth: Supabase (Postgres + supabase-py + Supabase Auth)
- LLM calls: Groq SDK (Llama 3.3 70B) for extraction/generation with strict JSON-schema
  outputs; `google-generativeai` (Gemini free tier) for brand research with search
  grounding. Centralize all LLM calls in `core/llm.py` with retry, rate-limit backoff,
  and provider fallback (Groq → Gemini).
- Scraping: Playwright (in GitHub Actions) + httpx + BeautifulSoup; Instaloader behind a
  feature flag
- Logo detection: OWL-ViT zero-shot (Hugging Face Transformers) in a Colab notebook —
  batch only, results written to Supabase
- Lead ranking: rule-based Evidence Score v1 → XGBoost learning-to-rank v2 (trained in
  Colab once ≥50 labeled outcomes exist)
- Pricing model: NumPyro (JAX) hierarchical Bayesian model, fit in Colab, posterior
  samples cached to Supabase; Streamlit simulator reads cached samples (no live MCMC)
- Deck rendering: python-pptx against `templates/master_deck.pptx`
- Embeddings/RAG (Pitch language memory): **Gemini `gemini-embedding-001` at
  `output_dimensionality=768`** + pgvector on Supabase. No local embedding models —
  sentence-transformers drags in torch and exceeds Streamlit free-tier memory.
  (Amended 2026-06-10; model updated 2026-06-11 after Google retired
  text-embedding-004 — the 768-dim schema is unchanged.)
- Scheduling: GitHub Actions cron (weekly Scout refresh), `workflow_dispatch` for
  manual runs

## DATABASE SCHEMA

Implemented in `supabase/schema.sql`. Tables: profiles, invite_codes, brands, evidence,
leads, tiers, pricing_posteriors, decks, outcomes, pitch_memory. RLS on every table:
viewers SELECT-only; analysts SELECT all, no writes; sponsorship INSERT/UPDATE on leads,
decks, outcomes (no DELETE); admin full. Service-role key only in `jobs/` (server-side),
never in the Streamlit app.

Approved schema amendments (2026-06-10):
- `pitch_memory.embedding` is **vector(768)** (Gemini text-embedding-004), not 384.
- `brands.is_demo` and `leads.is_demo` boolean default false — demo-data kill switch.
  Demo rows are badged in the UI and production purge is one DELETE per table.
  Refined 2026-06-11: demo leads generate only watermarked TEST decks ("DO NOT
  SEND" banner, TEST_ filename, prefixed email) for junior practice; real-lead
  deck generation is hard-blocked while core/chapter_facts.py contains any
  "[UPDATE ME" placeholder.
- `outcomes.event` enum is (sent, replied, meeting, signed, ghosted, rejected) to match
  the one-tap UI spec (original `opened_meeting` was split into its two real events).
- Idempotency constraints: `evidence` unique(brand_id, source_url),
  `leads` unique(brand_id, fest_target).

## UI REQUIREMENTS — DESIGN FOR A FIRST-YEAR JUNIOR

Test user: a first-year committee member on a phone, mid-fest, 30 seconds of attention.
- Zero-jargon labels ("Evidence Score", "How confident we are"). `?` help popover on
  every metric, one plain sentence.
- Lead Board = home workspace: searchable, filterable table (status, industry, score,
  owner), color-coded status chips, detail panel with evidence links, score breakdown,
  suggested tier, action buttons (Generate Deck / Log Outcome / Assign to me).
- Tier Simulator: sliders → live chart with shaded uncertainty band + one-line plain
  English summary. Reads cached posteriors only; responds in <1s.
- Deck Generator: progress stages, download PPTX + copyable email in an editable text
  area captioned "Review and edit before sending. Never send unread AI output."
  **No auto-send. Ever. Sending is a human act.**
- Outcomes: one-tap logging, optional deal value. Frictionless.
- Admin: invite codes, roles, rival-fest seed list, Scout run status, workflow_dispatch
  link.
- Mobile-friendly: no wide tables on phone, large tap targets, sensible st.cache_data.

## BUILD ORDER

Phase 1 Skeleton & Auth → Phase 2 Scout v1 → Phase 3 Pitch v1 → Phase 4 Price v1 →
Phase 5 The Loop → Phase 6 Hardening & Handoff. Work phase by phase; each phase ends
runnable; conventional commit at the end of every phase.

## ENGINEERING RULES

- Strict JSON-schema outputs for every LLM call; validate with pydantic; one retry with
  error feedback; on second failure log and skip — never crash the batch.
- Idempotent jobs: re-running Scout upserts, never duplicates (dedupe on
  `normalized_name` + evidence `source_url`).
- Defensive free-tier behavior: rate-limit sleeps; on 429 fall back to the other
  provider; if both fail, checkpoint progress and exit cleanly.
- Type hints everywhere; docstrings on public functions; small pure functions in
  `core/` covered by pytest (minimum: scoring rules, dedup/normalization, tier math).
- When uncertain about a product decision, choose the option that is simpler for a
  non-technical junior.

## KNOWN DEFERRED TRADEOFFS

- Email confirmation is OFF in Supabase Auth (instant phone signup). Consequence: a
  typo'd signup email cannot self-recover its password — admin resets it from the
  Supabase dashboard (documented in README troubleshooting).
- Sessions do not survive a hard refresh / new tab (tokens in st.session_state only).
  Accepted papercut; revisit in Phase 6. Do not add cookie-manager deps before then.
- Supabase free tier pauses after 7 idle days; the Phase 2 weekly cron doubles as
  keep-alive. Between Phase 1 and 2, manual usage covers it.

## ACCEPTANCE TEST

A first-year sponsorship junior with zero technical knowledge can: log in with an invite
code on their phone → open the Lead Board → pick a high-score lead and read its
evidence → generate a bespoke deck with priced tiers → download it and copy the email →
log the outcome after sending — all in under 10 minutes, without asking anyone for
help, at a total infrastructure cost of ₹0.
