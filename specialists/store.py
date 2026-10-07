"""Persistence for specialist agents (tables: user_agents, agent_runs,
agent_entries, search_cache). Same shape as supabase_client: thin
functions over the shared Supabase client, so tests can swap in a fake."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase_client import get_client, utcnow_iso


def _month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _day_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


# --- user_agents ------------------------------------------------------------

def list_user_agents(user_id: str) -> list:
    res = get_client().table("user_agents").select("*").eq("user_id", user_id).order("created_at").execute()
    return res.data or []


def get_user_agent(user_id: str, user_agent_id: str) -> Optional[dict]:
    res = (get_client().table("user_agents").select("*")
           .eq("user_id", user_id).eq("id", user_agent_id).limit(1).execute())
    return res.data[0] if res.data else None


def upsert_user_agent(user_id: str, agent_key: str, fields: dict) -> dict:
    row = {"user_id": user_id, "agent_key": agent_key, **fields, "updated_at": utcnow_iso()}
    res = get_client().table("user_agents").upsert(row, on_conflict="user_id,agent_key").execute()
    return res.data[0]


def update_user_agent(user_agent_id: str, fields: dict) -> Optional[dict]:
    res = (get_client().table("user_agents").update({**fields, "updated_at": utcnow_iso()})
           .eq("id", user_agent_id).execute())
    return res.data[0] if res.data else None


def delete_user_agent(user_id: str, user_agent_id: str) -> None:
    get_client().table("user_agents").delete().eq("user_id", user_id).eq("id", user_agent_id).execute()


def claim_due_agents(max_rows: int) -> list:
    """Atomically claim due (or stuck > 15 min) agents; see migration.sql."""
    return get_client().rpc("claim_due_agents", {"max_rows": max_rows}).execute().data or []


# --- agent_runs -------------------------------------------------------------

def create_run(row: dict) -> dict:
    return get_client().table("agent_runs").insert(row).execute().data[0]


def latest_runs(user_id: str, limit: int = 50) -> list:
    res = (get_client().table("agent_runs").select("*").eq("user_id", user_id)
           .order("created_at", desc=True).limit(limit).execute())
    return res.data or []


def last_ok_run(user_agent_id: str) -> Optional[dict]:
    res = (get_client().table("agent_runs").select("*").eq("user_agent_id", user_agent_id)
           .eq("status", "ok").order("created_at", desc=True).limit(1).execute())
    return res.data[0] if res.data else None


def mark_surfaced(run_ids: list) -> None:
    if run_ids:
        get_client().table("agent_runs").update({"surfaced_at": utcnow_iso()}).in_("id", run_ids).execute()


def _count_runs(user_id: str, since: str, triggers: tuple, user_agent_ids: Optional[list] = None) -> int:
    if user_agent_ids is not None and not user_agent_ids:
        return 0
    q = (get_client().table("agent_runs").select("id", count="exact").eq("user_id", user_id)
         .gte("created_at", since).neq("status", "skipped").in_("trigger", list(triggers)))
    if user_agent_ids is not None:
        q = q.in_("user_agent_id", user_agent_ids)
    return q.execute().count or 0


def runs_this_month(user_id: str, triggers: tuple, user_agent_ids: Optional[list] = None) -> int:
    return _count_runs(user_id, _month_start_iso(), triggers, user_agent_ids)


def runs_today(user_id: str) -> int:
    return _count_runs(user_id, _day_start_iso(), ("manual", "schedule", "alfred"))


# --- agent_entries ----------------------------------------------------------

def add_entry(row: dict) -> dict:
    return get_client().table("agent_entries").insert(row).execute().data[0]


def recent_entries(user_agent_id: str, limit: int = 200) -> list:
    res = (get_client().table("agent_entries").select("*").eq("user_agent_id", user_agent_id)
           .order("occurred_at", desc=True).limit(limit).execute())
    return res.data or []


# --- search_cache -----------------------------------------------------------

def cached_search(query_hash: str, max_age_hours: int) -> Optional[list]:
    res = get_client().table("search_cache").select("*").eq("query_hash", query_hash).limit(1).execute()
    if not res.data:
        return None
    fetched = datetime.fromisoformat(res.data[0]["fetched_at"])
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - fetched > timedelta(hours=max_age_hours):
        return None
    return res.data[0]["results"]


def store_search(query_hash: str, query: str, results: list) -> None:
    get_client().table("search_cache").upsert(
        {"query_hash": query_hash, "query": query, "results": results, "fetched_at": utcnow_iso()},
        on_conflict="query_hash",
    ).execute()
