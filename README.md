# Sponsor OS 🤝

The ACM SIGAI MUJ sponsorship pipeline: **Scout** finds sponsor leads from public
evidence, **Price** turns our event inventory into honestly-priced tiers, **Pitch**
builds a bespoke deck + email per lead — and every logged outcome makes it smarter.

Runs entirely on free tiers. Total infrastructure cost: **₹0**.

> This is the Phase 1 README (skeleton + auth). The full non-technical deployment
> guide ships in Phase 6.

## Setup (Phase 1)

1. **Create a Supabase project** (free tier) at [supabase.com](https://supabase.com).
2. **Apply the schema**: Dashboard → SQL Editor → paste all of
   [supabase/schema.sql](supabase/schema.sql) → Run.
3. **Turn off email confirmation**: Authentication → Sign In / Up → Email →
   disable **"Confirm email"**. (Invite codes replace email verification so juniors
   can sign up instantly on their phones.)
4. **Configure secrets**: copy `.env.example` to `.env` and fill in
   `SUPABASE_URL`, `SUPABASE_KEY` (anon), and `SUPABASE_SERVICE_KEY`
   (Dashboard → Settings → API). Never commit `.env`.
5. **Install and seed**:
   ```bash
   uv venv && uv pip install -r requirements.txt   # or: pip install -r requirements.txt
   python jobs/seed_demo.py
   ```
   The seed script prints **invite codes** (one per role) and fills the Lead Board
   with 10 clearly-marked 🧪 demo brands.
6. **Run the app**:
   ```bash
   streamlit run app/Home.py
   ```
   Sign up with the printed **admin** code first, then invite everyone else from
   the Admin page.

## Setup (Phase 2 — Scout)

Scout finds real sponsor leads automatically. One-time setup:

1. **Apply the Scout migration**: Supabase Dashboard → SQL Editor → paste all of
   [supabase/migrations/002_scout.sql](supabase/migrations/002_scout.sql) → Run.
   (Fresh installs that ran the current `schema.sql` already have it.)
2. **Get free LLM keys** and add to `.env`:
   - `GROQ_API_KEY` — [console.groq.com](https://console.groq.com) → API Keys
   - `GEMINI_API_KEY` — [aistudio.google.com](https://aistudio.google.com) → Get API key
3. **Test locally** (optional but recommended):
   ```bash
   python jobs/scout_refresh.py
   ```
   Watch the log; afterwards the Lead Board shows real brands with evidence links,
   and Admin shows the run summary.
4. **Automate on GitHub**: push this repo to GitHub, then in the repo →
   Settings → Secrets and variables → Actions, add: `SUPABASE_URL`,
   `SUPABASE_SERVICE_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`.
   Scout then runs every Monday ~8:30 AM IST, and on demand via
   Actions → "Scout refresh" → Run workflow. The weekly run also keeps the
   free Supabase project from pausing.
5. Optional: add `GITHUB_ACTIONS_URL` (your repo's Actions page URL) to
   Streamlit secrets to get a "Run Scout now" button on the Admin page.

## Roles

| Role | Can do |
|------|--------|
| `admin` | Everything: members, invite codes, corrections, destructive actions |
| `sponsorship` | Lead Board, generate decks, log outcomes, tier simulator |
| `analyst` | Lead Board (read-only), tier simulator, dashboards |
| `viewer` | Dashboards only (faculty advisors / chapter leads) |

## Troubleshooting

- **"Forgot password" / typo'd email at signup** — email confirmation is off, so
  there is no self-service reset. An admin fixes it in 30 seconds: Supabase
  Dashboard → Authentication → Users → find the user → **⋯ → Reset password**
  (or delete the user and issue a fresh invite code).
- **App says it's not connected to its database** — secrets are missing. Locally:
  check `.env`. On Streamlit Cloud: App settings → Secrets.
- **Everything logged me out after a refresh** — known Phase 1 papercut, not a
  bug: sessions live only in browser-tab memory for now. Just log in again.
- **Supabase project "paused"** — the free tier pauses after 7 idle days. Open
  the dashboard and click Restore. (From Phase 2, the weekly Scout cron keeps it
  awake.)
- **Scout stopped running on its own** — GitHub disables scheduled workflows
  after 60 days without repo activity. Our workflow makes a tiny "heartbeat"
  commit every scheduled run precisely to prevent this, but if it ever happens
  (e.g. the workflow was disabled manually): GitHub → Actions → Scout refresh →
  banner button **"Enable workflow"**. Worth a calendar reminder at semester
  start: check that the last Scout run is green.
- **Scout run says "partial"** — some site was down or an AI provider was
  rate-limited. Nothing is broken: every run re-checks everything, so the next
  run fills the gaps. Only investigate if it stays partial for weeks.

## Demo data

Seeded rows are marked **🧪 demo** in the UI and `is_demo=true` in the database.
**Never contact a brand based on demo evidence.** Going live? Run in the SQL editor:

```sql
delete from leads where is_demo;
delete from brands where is_demo;
```

## Tests

```bash
pytest -q
```
