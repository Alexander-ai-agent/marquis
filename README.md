# Marquis Backend

Flask REST API for Marquis — the AI business butler. Python 3.11+, Flask
3.0+, Supabase, Claude API (`claude-sonnet-4-6`). No websockets, no
streaming — every response is returned complete.

## 1. Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | yes | Supabase project URL |
| `SUPABASE_KEY` | yes | Supabase service role key (server-side, bypasses RLS) |
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `FRONTEND_URL` | yes | Exact frontend origin, no trailing slash — used for CORS |
| `JWT_SECRET` | yes | Random secret for signing auth tokens (e.g. `openssl rand -hex 32`) |
| `N8N_PHASE_COMPLETE_URL` | no | n8n's `/webhook/phase-complete` URL — see note below |
| `N8N_ONBOARDING_URL` | no | Optional n8n onboarding-flow webhook, called (best-effort) at signup |

The app refuses to start if any of the 5 required variables are missing.

## 2. Database setup

Two ways to set up the schema — pick one, not both:

**Option A — SQL editor.** Paste [`migration.sql`](migration.sql) into the
Supabase SQL editor and run it once.

**Option B — `setup_database.py`.** Automates the same schema plus RLS
policies, an admin test user, and a verification pass. Needs one extra
secret beyond the app's own env vars: `SUPABASE_DB_URL`, a direct Postgres
connection string (Project Settings → Database → Connection string → URI)
— required because `SUPABASE_URL`/`SUPABASE_KEY` are the PostgREST REST
API, which can't run DDL (`CREATE TABLE`, `CREATE POLICY`).

```bash
pip install -r requirements-setup.txt
python setup_database.py
```

It runs 5 steps in order and exits non-zero if verification fails (safe to
use as a Railway pre-deploy/one-off job):

1. Create `users`, `conversations`, `phases`, `activity_logs`,
   `prompt_improvements` + indexes (all `IF NOT EXISTS` — safe to re-run)
2. Enable RLS and create owner-only policies on `users`, `conversations`,
   `phases`, `activity_logs` (`prompt_improvements` gets RLS enabled with
   no policy — fail-closed/admin-only by default, since it's a global
   table, not user-owned)
3. Attempt to enable backups (point-in-time recovery) via the Supabase
   Management API — **skipped by default**; needs `SUPABASE_ACCESS_TOKEN`
   (a personal access token, not `SUPABASE_KEY`) and `SUPABASE_PROJECT_REF`.
   Verify the endpoint against current Supabase docs and your plan tier
   before relying on this — it's a best-effort call, not guaranteed.
4. Create one admin test user, if `ADMIN_EMAIL`/`ADMIN_PASSWORD` are set
   (skipped otherwise — no hardcoded default credentials)
5. Verify all 5 tables exist via `information_schema.tables`

Either way: RLS is enabled on every table. The app itself queries with the
service role key (bypasses RLS) — the policies matter only for direct
client access with a scoped anon/authenticated key, and assume
`auth.uid()` is set to match `users.id` in that scenario (this app's own
JWT auth doesn't set it).

## 3. Local development

```bash
pip install -r requirements.txt
python app.py
```

Runs on `http://localhost:5000` (or `$PORT`).

## 3b. Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite (`conftest.py` + `tests/`) never touches the real network: an
in-memory `FakeDB` fixture stands in for Supabase (patched into every
module that imports a `supabase_client` function), and Claude is mocked
via a `mock_claude` fixture patching the app's `get_butler_response` call.
No `.env`/live credentials are needed to run it — `conftest.py` sets the
required env vars itself before `app` is ever imported.

- `tests/test_auth.py` — login, signup, missing-field validation, JWT expiration
- `tests/test_endpoints.py` — dashboard, phases, profile, CORS
- `tests/test_integration.py` — `/conversation` (Claude mocked) end-to-end,
  a full signup→login→profile→phases→dashboard→conversation flow, and
  direct unit tests for the 4 agents' branch logic and `butler.py`'s
  prompt/message assembly

With coverage:

```bash
pytest --cov=. --cov-report=term-missing
```

Currently **96%** on the measured modules (target was 80%). `supabase_client.py`
is excluded from that number via `.coveragerc` (same reasoning as
`webhook_client.py` and `setup_database.py`): it's a thin wrapper around
the real Supabase query builder that the fake DB exists specifically to
avoid calling, so its *real* behavior is deliberately untested — only its
interface shape (via `FakeDB` matching it method-for-method) is exercised
indirectly through every endpoint test. If real-database behavior needs
verification, that's an integration test against an actual Supabase
project, not something this mocked suite can honestly claim to cover.

## 4. Deploying to Railway

1. Push this directory to GitHub (or connect Railway to it directly).
2. Create a Railway project from that repo.
3. Set the environment variables above in Railway's Variables tab.
4. Railway detects the `Procfile` (`web: gunicorn app:app --bind 0.0.0.0:$PORT`)
   and redeploys on every push.
5. Confirm `GET /health` returns `{"status": "ok"}`.

## API surface

The original 6 endpoints, plus 4 added for the Sep 24 frontend redesign
(marked *new*).

Base path: `/api/v1/marquis`

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/login` | — | `{email, password}` → `{token, user_id, name}` |
| POST | `/auth/signup` | — | `{email, password, name}` → `{token, user_id}` |
| GET | `/user/profile` | Bearer | Profile fields for the logged-in user |
| GET | `/dashboard` | Bearer | Greeting, phase ring, stat cards, 4 agent insights, quick links |
| POST | `/conversation` | Bearer | `{message, conversation_history}` → butler reply + agent context |
| GET | `/phases` | Bearer | This user's phases, with `progress_percent` |
| POST | `/phases/<id>/complete` | Bearer | *new*: marks the user's **active** phase complete (actual_days from started_at), activates the next phase, logs activity, notifies the n8n phase webhook. 400 if not active, 404 if not theirs |
| GET | `/agents/signals` | Bearer | *new*: real data for the Living Canvas's idle agent presences: `{activity[14], phases[], blocker_topics[], coverage[]}`. Empty lists mean "nothing to read yet" |
| POST | `/onboarding/questions` | Bearer | *new*: `{business_type, stage, description}` → `{questions: [3-5]}` from Claude; saves the profile fields. 502 if generation fails |
| POST | `/onboarding/pathway` | Bearer | *new*: profile + `answers[{question, answer}]` → `{assessment, phase{name, estimated_days, actions, tools[{name,cost}], success}, this_week}`; marks onboarding complete and creates phase 1 if the user has none |

**`/conversation` now also returns `visualization`** (null on ordinary
turns). The butler may end a reply with one sentinel line,
`<<CANVAS {json}>>`, only when it is concretely explaining something
visual. `butler.parse_visualization()` always strips that line from the
stored and returned prose, and returns the payload only if it passes an
allow-list and shape checks (`revenue_projection`, `phase_timeline`,
`blocker_heat`, `activity_pulse`, `coverage`). Anything malformed → `null`
(fail-soft; the frontend stays idle). `phase_timeline` data is filled
server-side from the phases table, never from the model.

**`/dashboard` now returns exactly the 3 stat cards the dashboard shows**
(days active, combined gap vs estimate, sessions this week, each with a
`trend`), plus `sub`, `name`, `active_phase_name` and
`last_butler_exchange_at`. Quick-link `action`s are real frontend page ids.
The Enhancement row now comes from the Enhancement Suggester agent.

**Fixed:** the Enhancement Suggester never matched anything for real
users. Onboarding saves display labels ("SaaS / Software", "Launched")
while `enhancement_library` is keyed "SaaS" / "Launch";
`normalize_business_type()` / `normalize_stage()` now map between them.

Errors are `{"error": "message"}` with standard HTTP status codes
(400/401/500). Rate limiting: 100 requests/minute, keyed by the JWT's
subject when present, otherwise by IP.

## SMTP for the n8n workflows

Email sending (milestone messages, re-engagement, admin digests) is
handled entirely by the n8n workflows in `../n8n/`, not by this Flask
backend. Full setup — SendGrid signup, Railway env vars
(`SMTP_HOST`/`SMTP_USER`/`SMTP_PASS`/`SMTP_FROM`/`ADMIN_EMAIL`), creating
the shared `MARQUIS SMTP` n8n credential (`../n8n/setup_n8n_smtp.py`
automates this), and testing — is documented in
[`../n8n/SMTP_SETUP.md`](../n8n/SMTP_SETUP.md).

## The 4 background agents

Run automatically inside `/conversation` and `/dashboard` (`agents/`):

1. `performance_analyst.py` — phase timing vs estimate, activity pattern
2. `pathway_optimizer.py` — is the phase sequence still on track
3. `blocker_detector.py` — inactivity / repeated-topic stuck signals
4. `enhancement_suggester.py` — one standard-for-this-stage suggestion from
   `enhancement_library.py`, matched against `business_type` + `stage`

Their output is folded into `agent_context` in the `/conversation`
response and the hardcoded butler system prompt (`butler.py`). Approved
rows from `prompt_improvements` (admin-curated, populated by the n8n daily
improvement workflow) are appended to the system prompt as an additional
"apply these" section, and the single latest approved row is also surfaced
as the dashboard's `enhancement` agent insight.

**Known gap in the `stage` vocabulary**: nothing in this spec defines what
values `users.stage` holds. `enhancement_library.py` is keyed on
`Foundation | Build | Launch | Revenue | Scale`; if `stage` is written with
different values by whatever process sets it, the enhancement agent will
simply find no match and surface nothing (`surface_now: null`) rather than
erroring — worth aligning once the write path for `business_type`/`stage`
exists.

## The n8n phase-completion webhook — a gap worth confirming

The brief says this backend calls `/webhook/phase-complete` "when a user
finishes a phase," and `webhook_client.py` implements that call
(`notify_phase_complete`, POSTing exactly the body n8n's
`marquis_phase_completion.json` expects: `user_id`, `phase_id`,
`phase_name`, `estimated_days`, `actual_days`). n8n owns marking the phase
row `complete` and emailing the user from there.

**But none of the 6 required endpoints marks a phase complete**, and the
brief says not to add endpoints beyond the 6 listed. So `webhook_client.py`
is implemented and ready, but nothing calls it yet — there's no endpoint
in this spec that represents "the user finished a phase." Wiring it up
needs a decision: either add a 7th endpoint for this (e.g.
`POST /phases/<id>/complete`), or trigger it from inside an existing one
(e.g. detect completion from a signal in `/conversation`'s message body).
