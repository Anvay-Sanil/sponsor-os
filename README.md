# Sponsor OS 🤝

The ACM SIGAI MUJ sponsorship pipeline. Three stages, one loop:

- **Scout** finds sponsor leads from public evidence (rival fest sites, news) — weekly, automatically.
- **Price** turns our event inventory into honest reach estimates with uncertainty ranges.
- **Pitch** builds a bespoke branded deck + cold email per lead, in ~30 seconds.
- **The Loop**: every outcome you log makes the ranking and the pitch language smarter.

Total infrastructure cost: **₹0, forever** (Supabase, Streamlit Cloud, GitHub Actions,
Groq and Gemini free tiers, Colab for heavy jobs).

---

## Deploy from zero (~30 minutes, no coding)

You need: a chapter Google account, a chapter GitHub account/org (see
[Account ownership](#account-ownership--this-must-outlive-you)), and this repository.

### A. Database (Supabase, ~7 min)

1. [supabase.com](https://supabase.com) → New project → name `sponsor-os`,
   region **Mumbai**, free plan. Save the database password somewhere safe.
2. SQL Editor → New query → paste ALL of [supabase/schema.sql](supabase/schema.sql)
   → Run → expect `Success. No rows returned`.
   *(Fresh installs need only this one file — it includes every migration.)*
3. Authentication → Sign In / Up → Email → turn **OFF "Confirm email"**.
4. Storage → confirm a private bucket `decks` exists (schema migration 003 creates it
   on existing projects; on a brand-new project create it: New bucket → `decks` → private).
5. Project Settings → API: copy the **Project URL**, **anon** key, and
   **service_role** key.

### B. Free AI keys (~5 min)

- **Groq**: [console.groq.com](https://console.groq.com) → API Keys → create → copy (`gsk_…`).
- **Gemini**: [aistudio.google.com](https://aistudio.google.com) → Get API key → copy (`AIza…`).

### C. GitHub (~5 min)

1. Fork or push this repo to the **chapter's** GitHub org, private.
2. Repo → Settings → Secrets and variables → Actions → add four secrets:
   `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`.
3. Actions tab → enable workflows. Scout now runs every Monday ~8:30 AM IST
   (and keeps the free database awake).

### D. The app (Streamlit Community Cloud, ~7 min)

1. [share.streamlit.io](https://share.streamlit.io) → sign in with the chapter GitHub
   → **New app** → pick the repo, branch `main`, main file **`app/Home.py`**.
2. Advanced settings → Secrets → paste (with your values):
   ```toml
   SUPABASE_URL = "https://YOUR-REF.supabase.co"
   SUPABASE_KEY = "YOUR-ANON-KEY"
   GROQ_API_KEY = "gsk_..."
   GEMINI_API_KEY = "AIza..."
   GITHUB_ACTIONS_URL = "https://github.com/YOUR-ORG/sponsor-os/actions"
   ```
   ⚠️ The **service_role key never goes in Streamlit secrets** — it belongs only in
   GitHub Actions secrets and a local `.env`.
3. Deploy. First build takes a few minutes.

### E. First data + first admin (~5 min)

On any laptop with Python: copy `.env.example` → `.env`, fill the Supabase values, then

```bash
pip install -r requirements.txt
python jobs/seed_demo.py        # demo data + invite codes printed to console
```

Open the deployed app → **Sign up with invite code** using the printed **admin** code.
Generate codes for everyone else from the Admin page. Trigger the first Scout run
from GitHub → Actions → Scout refresh → Run workflow.

### F. Before going live with real outreach

1. Fill **[core/chapter_facts.py](core/chapter_facts.py)** with REAL numbers (real
   gate counts, real follower counts — sponsors verify; never inflate). Decks for
   real brands stay locked until every `[UPDATE ME]` is gone.
2. Purge demo data (SQL Editor):
   `delete from leads where is_demo; delete from brands where is_demo;`
3. Refit pricing with real observations: run
   [notebooks/fit_pricing_model.ipynb](notebooks/fit_pricing_model.ipynb) in Colab.

---

## Daily use (the 30-second version per page)

- **Lead Board** — every brand, ranked by Evidence Score; tap a row for the proof.
  `Assign to me` to claim, `Generate Deck` to pitch, `Log Outcome` after contact.
- **Deck Generator** — pick lead → ~30s → download PPTX + copy the email.
  **Review and edit before sending. Never send unread AI output.** There is no
  send button anywhere, on purpose. 🧪 demo leads make watermarked practice decks.
- **Tier Simulator** — build a package, see honest reach ranges and ₹-per-view.
- **Outcomes** — after every pitch: one tap. Mis-tap? Undo within 10 minutes.
  Every real tap moves the Home progress bar toward Smart Ranking (activates at 50).
- **Admin** — invite codes, roles, rival-fest seeds, last Scout run.

## Roles

| Role | Can do |
|------|--------|
| `admin` | Everything: members, codes, corrections, destructive actions |
| `sponsorship` | Leads, decks, outcomes, simulator |
| `analyst` | Read-only leads, simulator, dashboards |
| `viewer` | Dashboards only (faculty advisors) |

---

## Account ownership — this must outlive you

The whole point of Sponsor OS is that chapter knowledge survives graduation. That
fails trivially if the accounts are personal. **Rule: Supabase, Google AI Studio
(Gemini key), Groq, GitHub, and Streamlit Cloud all belong to chapter-controlled
identities** — a chapter Gmail and a chapter GitHub org (free) — never to whoever
happened to build or deploy it.

### Committee transition ritual (every handover, ~30 min)

1. Transfer/verify chapter ownership of all five accounts above.
2. **Rotate** the Supabase service_role key and both AI keys; update GitHub Actions
   secrets, Streamlit secrets, and local `.env`s.
3. Make the incoming lead an `admin` in the app; demote/remove departed members.
4. Regenerate invite codes (old ones expire in 30 days anyway).
5. Walk through [DEMO.md](DEMO.md) together once.

### Maintenance calendar

| When | What |
|------|------|
| Semester start | Check GitHub → Actions: last Scout run green? (Heartbeat commits keep the schedule alive, but verify.) |
| Before real outreach | Demo purge + chapter facts filled (section F) |
| After each fest | Refit pricing notebook with the new footfall/reel numbers |
| At 50 logged outcomes | Run [notebooks/train_ranker.ipynb](notebooks/train_ranker.ipynb) — Smart Ranking activates |
| Committee transition | The ritual above |

---

## Troubleshooting

- **Where's the real error?** Juniors see friendly messages, never tracebacks — the
  full trace is in **Streamlit Cloud → Manage app → Logs** (and in `scout_runs.log`
  for Scout, GitHub Actions logs for the cron). Start every debugging session there.
- **Forgot password / typo'd signup email** — email confirmation is off, so no
  self-service reset: Supabase → Authentication → Users → ⋯ → Reset password.
- **"Not connected to its database"** — secrets missing/wrong (app settings → Secrets).
- **Logged out after refresh** — shouldn't happen anymore (7-day cookie); if it
  persists, the browser is blocking cookies.
- **Supabase paused** — free tier pauses after 7 idle days; dashboard → Restore.
  The Monday cron normally prevents this.
- **Scout stopped running** — GitHub → Actions → Scout refresh → "Enable workflow"
  button. Run says `partial`? A site or AI provider was down; next run self-heals.
- **AI says it's busy** — free-tier rate limits; wait a minute. Persistent? Check
  both API keys are valid.

## Contributing rules (enforced by tests)

- **Never `unsafe_allow_html=True`** — this app renders scraped and LLM-derived
  text; raw HTML would open an injection surface (a test fails the build if it appears).
- **No heavy ML deps in requirements.txt** (torch/jax/xgboost live in Colab notebooks).
- Friendly error + `logger.exception` in every failure path; honest numbers only —
  intervals over point estimates, evidence links over claims.
- Run `pytest -q` before committing (176 tests).

## Post-handoff backlog (recorded decisions, not forgotten ideas)

- Playwright rendering for JS-heavy fest sites (when a seed actually needs it)
- Instagram enrichment behind the feature flag (brittle; fest sites are primary)
- Fuzzy brand-name matching (exact normalized match has been sufficient)
- Learning-to-rank objective + per-lead pricing (≥500 outcomes)
- Audience-overlap modeling in bundle reach (currently "up to" language absorbs it)

## Tests

```bash
pytest -q
```
