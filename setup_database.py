"""One-time Supabase setup: schema, RLS policies, admin user, verification.

Run once (e.g. on Railway): python setup_database.py

Why this needs a Postgres connection, not just SUPABASE_URL/SUPABASE_KEY:
those two are the PostgREST REST API — fine for row inserts/selects, but
PostgREST cannot run DDL (CREATE TABLE, CREATE POLICY). Steps 1-2 use a
direct Postgres connection (SUPABASE_DB_URL) via psycopg2; steps 4-5 use
the ordinary supabase-py REST client, same as the app itself.
"""
import os
import sys

import bcrypt
import psycopg2
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(encoding="utf-8-sig")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


SUPABASE_URL = _env("SUPABASE_URL")
SUPABASE_KEY = _env("SUPABASE_KEY")
SUPABASE_DB_URL = _env("SUPABASE_DB_URL")

# Optional: admin test user. Both must be set to create one — there is no
# default password, since a hardcoded fallback credential would be a
# security hole in anything that reaches Railway.
ADMIN_EMAIL = _env("ADMIN_EMAIL")
ADMIN_PASSWORD = _env("ADMIN_PASSWORD")
ADMIN_NAME = _env("ADMIN_NAME", "Admin")

# Optional: Supabase Management API access, only used to attempt enabling
# backups. This is a different secret from SUPABASE_KEY (a personal access
# token, scoped to your whole account) — see enable_backups() below.
SUPABASE_ACCESS_TOKEN = _env("SUPABASE_ACCESS_TOKEN")
SUPABASE_PROJECT_REF = _env("SUPABASE_PROJECT_REF")

REQUIRED_VARS = ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_DB_URL")

# Mirrors marquis_schema.sql, plus the public.users table it assumes exists
# but never defines. IF NOT EXISTS throughout so this script is safe to
# re-run (e.g. a redeploy re-running the Railway start command).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS public.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE NOT NULL,
  password_hash text,
  name text,
  business_type text,
  stage text,
  description text,
  onboarding_complete boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES public.users(id),
  role text,
  content text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.phases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES public.users(id),
  phase_number int,
  phase_name text,
  status text,
  estimated_days int,
  actual_days int,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.activity_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES public.users(id),
  event_type text,
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.prompt_improvements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  suggestion text,
  reason text,
  priority text,
  approved boolean DEFAULT false,
  date date DEFAULT current_date,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_created ON public.conversations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_created ON public.conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_phases_user_status ON public.phases(user_id, status);
CREATE INDEX IF NOT EXISTS idx_activity_logs_user_created ON public.activity_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_logs_created ON public.activity_logs(created_at);
"""

# RLS on the 4 tables named in the task, keyed exactly as specified
# (conversations/phases/activity_logs: user_id = auth.uid(); users: id =
# auth.uid()). prompt_improvements also gets RLS enabled with no policy —
# fail-closed by default (admin-only via the service role key) since it's
# global, not user-owned, and the task didn't ask for a per-user policy on
# it. DROP POLICY IF EXISTS first since Postgres has no CREATE POLICY IF
# NOT EXISTS, keeping this idempotent on re-run.
#
# Caveat: this backend authenticates with its own JWT and talks to
# Supabase with the service role key, which bypasses RLS entirely. These
# policies only take effect if something queries Supabase directly with an
# anon/authenticated key — they assume auth.uid() is set to match
# users.id in that scenario (e.g. via Supabase Auth or a matching claim).
RLS_SQL = """
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activity_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.prompt_improvements ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_own_row" ON public.users;
CREATE POLICY "users_own_row" ON public.users
  FOR ALL USING (id = auth.uid());

DROP POLICY IF EXISTS "conversations_own_rows" ON public.conversations;
CREATE POLICY "conversations_own_rows" ON public.conversations
  FOR ALL USING (user_id = auth.uid());

DROP POLICY IF EXISTS "phases_own_rows" ON public.phases;
CREATE POLICY "phases_own_rows" ON public.phases
  FOR ALL USING (user_id = auth.uid());

DROP POLICY IF EXISTS "activity_logs_own_rows" ON public.activity_logs;
CREATE POLICY "activity_logs_own_rows" ON public.activity_logs
  FOR ALL USING (user_id = auth.uid());
"""

EXPECTED_TABLES = ["users", "conversations", "phases", "activity_logs", "prompt_improvements"]


def require_env() -> None:
    """Exit with a clear message if any required env var is missing."""
    missing = [name for name in REQUIRED_VARS if not globals()[name]]
    if missing:
        print(f"Missing required environment variable(s): {', '.join(missing)}")
        sys.exit(1)


def run_schema(conn) -> None:
    """Step 1: create tables and indexes."""
    print("1/5  Creating tables and indexes ...")
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()
    print("     done.")


def run_rls(conn) -> None:
    """Step 2: enable RLS and create owner-only policies."""
    print("2/5  Enabling RLS and creating policies ...")
    with conn.cursor() as cur:
        cur.execute(RLS_SQL)
    conn.commit()
    print("     done.")


def enable_backups() -> None:
    """Step 3: attempt to enable point-in-time recovery via the Management API.

    This is a project-level setting, not something SQL can turn on — it
    requires a Supabase personal access token (SUPABASE_ACCESS_TOKEN) and
    project ref (SUPABASE_PROJECT_REF), which are different secrets from
    SUPABASE_URL/SUPABASE_KEY. Skipped with instructions if not provided.
    Also requires a paid plan — verify the endpoint/response against
    current Supabase Management API docs before relying on this in CI.
    """
    print("3/5  Enabling backups ...")
    if not (SUPABASE_ACCESS_TOKEN and SUPABASE_PROJECT_REF):
        print("     SKIPPED — SUPABASE_ACCESS_TOKEN and SUPABASE_PROJECT_REF are not set.")
        print("     Backups/PITR are a project-level setting, toggled via the Supabase")
        print("     Dashboard or Management API (a personal access token, not the DB key).")
        ref = SUPABASE_PROJECT_REF or "<project-ref>"
        print(f"     Enable manually: https://supabase.com/dashboard/project/{ref}/settings/database")
        return

    import requests

    url = f"https://api.supabase.com/v1/projects/{SUPABASE_PROJECT_REF}/config/database/pitr"
    try:
        resp = requests.put(
            url,
            headers={
                "Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"enabled": True},
            timeout=10,
        )
        if resp.ok:
            print("     Point-in-time recovery enabled.")
        else:
            print(f"     Management API call failed ({resp.status_code}): {resp.text}")
            print("     Verify this endpoint against current Supabase Management API docs")
            print("     and confirm your plan tier supports PITR.")
    except requests.RequestException as e:
        print(f"     Management API call errored: {e}")


def create_admin_user() -> None:
    """Step 4: insert an admin test user via the REST client, if credentials are set."""
    print("4/5  Creating admin test user ...")
    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        print("     SKIPPED — set ADMIN_EMAIL and ADMIN_PASSWORD to create one.")
        return

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    existing = client.table("users").select("id").eq("email", ADMIN_EMAIL).limit(1).execute()
    if existing.data:
        print(f"     {ADMIN_EMAIL} already exists — skipping.")
        return

    password_hash = bcrypt.hashpw(ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    client.table("users").insert(
        {
            "email": ADMIN_EMAIL,
            "password_hash": password_hash,
            "name": ADMIN_NAME,
            "onboarding_complete": True,
        }
    ).execute()
    print(f"     Created: {ADMIN_EMAIL}")


def verify_schema(conn) -> None:
    """Step 5: confirm every expected table exists; exit non-zero if any are missing."""
    print("5/5  Verifying schema ...")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
        )
        found = {row[0] for row in cur.fetchall()}

    missing = []
    for table in EXPECTED_TABLES:
        present = table in found
        print(f"     {'OK     ' if present else 'MISSING'} {table}")
        if not present:
            missing.append(table)

    if missing:
        print(f"\nSchema verification FAILED — missing: {', '.join(missing)}")
        sys.exit(1)
    print("\nSchema verification passed.")


def main() -> None:
    require_env()
    conn = psycopg2.connect(SUPABASE_DB_URL)
    try:
        run_schema(conn)
        run_rls(conn)
        enable_backups()
        create_admin_user()
        verify_schema(conn)
    finally:
        conn.close()
    print("\nSetup complete.")


if __name__ == "__main__":
    main()
