-- Marquis backend schema.
-- Matches marquis_schema.sql (n8n/marquis_schema.sql) exactly for
-- conversations, phases, activity_logs, and prompt_improvements.
--
-- marquis_schema.sql assumes "public.users(id) already exists" but does
-- not define it — this migration adds it, sized to exactly what the
-- signup/login/user-profile endpoints need: password_hash (nowhere else
-- to store it), plus the profile fields GET /user/profile returns
-- (business_type, stage, description, onboarding_complete).

CREATE TABLE public.users (
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

CREATE TABLE public.conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES public.users(id),
  role text,
  content text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE public.phases (
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

CREATE TABLE public.activity_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES public.users(id),
  event_type text,
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE public.prompt_improvements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  suggestion text,
  reason text,
  priority text,
  approved boolean DEFAULT false,
  date date DEFAULT current_date,
  created_at timestamptz DEFAULT now()
);

-- Indexes to support the n8n workflow queries (conversation windows,
-- stuck-user detection, weekly digest range scans).
CREATE INDEX idx_conversations_user_created ON public.conversations(user_id, created_at DESC);
CREATE INDEX idx_conversations_created ON public.conversations(created_at);
CREATE INDEX idx_phases_user_status ON public.phases(user_id, status);
CREATE INDEX idx_activity_logs_user_created ON public.activity_logs(user_id, created_at DESC);
CREATE INDEX idx_activity_logs_created ON public.activity_logs(created_at);

-- RLS: enabled on every user-owned table, owner-only access.
-- Note: this Flask backend authenticates with its own JWT and talks to
-- Supabase using the service role key, which bypasses RLS by design (the
-- Flask layer is the authorization boundary). These policies matter if a
-- client ever queries Supabase directly with an anon/authenticated key,
-- and assume auth.uid() is set to match users.id in that scenario.
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activity_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.prompt_improvements ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own row" ON public.users
  FOR ALL USING (auth.uid() = id);

CREATE POLICY "Users manage own phases" ON public.phases
  FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users manage own conversations" ON public.conversations
  FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users manage own activity_logs" ON public.activity_logs
  FOR ALL USING (auth.uid() = user_id);

-- prompt_improvements is global (admin-curated, no user_id column) — every
-- authenticated user may read approved rows; writes are admin-only via the
-- service role key (used by the n8n workflow and Supabase Studio).
CREATE POLICY "Authenticated users read approved improvements" ON public.prompt_improvements
  FOR SELECT USING (approved = true);

-- --------------------------------------------------------------------------
-- Incremental additions (Sep 24-25 2026 redesign)
-- --------------------------------------------------------------------------

-- Butler naming (MARQUIS_product.md "Butler Screen Redesign"). ADD COLUMN
-- IF NOT EXISTS, not part of the CREATE TABLE above, so this also applies
-- to an already-deployed public.users table.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS butler_name text DEFAULT 'Reeves';

-- Custom AI Agents (MARQUIS_product.md "Custom AI Agents") — user-defined
-- agents beyond the 4 core agents. Custom agent builder is Phase 2 per the
-- same doc; this table is added now so the schema is ready ahead of it.
CREATE TABLE IF NOT EXISTS public.custom_agents (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid REFERENCES public.users(id) ON DELETE CASCADE,
  name text NOT NULL,
  focus text NOT NULL,
  data_sources jsonb DEFAULT '[]',
  created_at timestamptz DEFAULT now()
);

-- Tool connections (MARQUIS_product.md "Connected Data") — Phase 2 external
-- tool integrations (Stripe, HubSpot, Linear, broker APIs, etc.).
CREATE TABLE IF NOT EXISTS public.tool_connections (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid REFERENCES public.users(id) ON DELETE CASCADE,
  tool_name text NOT NULL,
  access_token text,
  refresh_token text,
  connected_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_custom_agents_user ON public.custom_agents(user_id);
CREATE INDEX IF NOT EXISTS idx_tool_connections_user ON public.tool_connections(user_id);

ALTER TABLE public.custom_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tool_connections ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own custom_agents" ON public.custom_agents
  FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users manage own tool_connections" ON public.tool_connections
  FOR ALL USING (auth.uid() = user_id);

-- ======================================================================
-- Specialist agents (Oct 2026). Additive only: new tables + one function.
-- The catalog lives in code (agents/catalog.py); these hold each user's
-- choices, every run's output, what they enter, and a shared search cache.
-- RLS on with no public policies: only the backend's service key reads/writes.
-- ======================================================================
CREATE TABLE IF NOT EXISTS public.user_agents (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  agent_key      text NOT NULL,
  settings       jsonb NOT NULL DEFAULT '{}',
  run_mode       text NOT NULL DEFAULT 'on_demand' CHECK (run_mode IN ('on_demand','scheduled')),
  interval_hours int CHECK (interval_hours BETWEEN 6 AND 168),
  state          text NOT NULL DEFAULT 'IDLE' CHECK (state IN ('IDLE','WORKING','FLAGGED')),
  enabled        boolean NOT NULL DEFAULT true,
  last_run_at    timestamptz,
  next_run_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, agent_key)
);

CREATE TABLE IF NOT EXISTS public.agent_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_agent_id uuid NOT NULL REFERENCES public.user_agents(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  trigger       text NOT NULL CHECK (trigger IN ('manual','schedule','alfred')),
  status        text NOT NULL CHECK (status IN ('ok','error','skipped')),
  output        jsonb NOT NULL DEFAULT '{}',
  flagged       boolean NOT NULL DEFAULT false,
  model         text,
  search_calls  int NOT NULL DEFAULT 0,
  input_tokens  int NOT NULL DEFAULT 0,
  output_tokens int NOT NULL DEFAULT 0,
  surfaced_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.agent_entries (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_agent_id uuid NOT NULL REFERENCES public.user_agents(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  kind          text NOT NULL CHECK (kind IN ('metric','log')),
  metric        text,
  value         numeric,
  body          jsonb NOT NULL DEFAULT '{}',
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.search_cache (
  query_hash text PRIMARY KEY,
  query      text NOT NULL,
  results    jsonb NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_agents_user ON public.user_agents(user_id);
CREATE INDEX IF NOT EXISTS idx_user_agents_due ON public.user_agents(next_run_at)
  WHERE enabled AND run_mode = 'scheduled';
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON public.agent_runs(user_agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_month ON public.agent_runs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_entries_agent ON public.agent_entries(user_agent_id, occurred_at DESC);

-- Claim due scheduled agents atomically (SKIP LOCKED: two workers never take
-- the same row). Also reclaims any agent stuck WORKING > 15 minutes, so a
-- crashed run is retried rather than leaving the agent stuck.
CREATE OR REPLACE FUNCTION public.claim_due_agents(max_rows int)
RETURNS SETOF public.user_agents LANGUAGE sql AS $$
  UPDATE public.user_agents u
     SET state = 'WORKING',
         next_run_at = now() + make_interval(hours => COALESCE(u.interval_hours, 24)),
         updated_at = now()
   WHERE u.id IN (
     SELECT id FROM public.user_agents
      WHERE enabled AND (
              (run_mode = 'scheduled' AND state <> 'WORKING' AND next_run_at <= now())
           OR (state = 'WORKING' AND updated_at < now() - interval '15 minutes'))
      ORDER BY next_run_at NULLS FIRST
      LIMIT max_rows
      FOR UPDATE SKIP LOCKED)
  RETURNING u.*;
$$;

ALTER TABLE public.user_agents   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_runs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.search_cache  ENABLE ROW LEVEL SECURITY;
