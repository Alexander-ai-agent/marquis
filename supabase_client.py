"""Supabase access layer.

All queries go through supabase-py's query builder, which parameterizes
values internally — no raw SQL string interpolation happens here.

Table names and columns follow marquis_schema.sql: users, phases,
conversations, activity_logs, prompt_improvements. Phase status values are
'future' | 'active' | 'complete' (matching the n8n workflows, which filter
on status=eq.active and set status='complete' on completion).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client, ClientOptions, create_client

from config import Config

_client: Optional[Client] = None

# supabase-py's ClientOptions.postgrest_client_timeout defaults to 120
# seconds. Against a malformed/wrong SUPABASE_URL, that means every
# request HANGS for up to 2 minutes before failing — which Railway's edge
# or Gunicorn's worker timeout then kills first, surfacing as an
# unexplained request timeout rather than a clear, fast error. This is the
# most likely cause of the reported signup/login hang: a short, explicit
# timeout turns a silent 2-minute hang into a fast, catchable exception.
_CLIENT_OPTIONS = ClientOptions(postgrest_client_timeout=10)


def get_client() -> Client:
    """Return a lazily-initialized singleton Supabase client with a short request timeout."""
    global _client
    if _client is None:
        _client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY, options=_CLIENT_OPTIONS)
    return _client


def utcnow_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string for Supabase timestamp columns."""
    return datetime.now(timezone.utc).isoformat()


def days_since(iso_timestamp: Optional[str]) -> Optional[float]:
    """Days elapsed between an ISO timestamp and now (UTC), or None if unset."""
    if not iso_timestamp:
        return None
    started = datetime.fromisoformat(iso_timestamp)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds() / 86400


# --- Users ------------------------------------------------------------------

def create_user(email: str, password_hash: str, name: str) -> dict:
    """Insert a new user and return the created row."""
    payload = {
        "email": email,
        "password_hash": password_hash,
        "name": name,
        "onboarding_complete": False,
        "created_at": utcnow_iso(),
    }
    response = get_client().table("users").insert(payload).execute()
    return response.data[0]


def get_user_by_email(email: str) -> Optional[dict]:
    """Look up a user by email, or None if not found."""
    response = get_client().table("users").select("*").eq("email", email).limit(1).execute()
    return response.data[0] if response.data else None


def get_user_by_id(user_id: str) -> Optional[dict]:
    """Look up a user by id, or None if not found."""
    response = get_client().table("users").select("*").eq("id", user_id).limit(1).execute()
    return response.data[0] if response.data else None


# --- Phases -------------------------------------------------------------

def get_phases(user_id: str) -> list:
    """Return all phases for a user, ordered by phase_number."""
    response = (
        get_client()
        .table("phases")
        .select("*")
        .eq("user_id", user_id)
        .order("phase_number")
        .execute()
    )
    return response.data


def get_active_phase(user_id: str) -> Optional[dict]:
    """Return the user's single 'active' phase, if any."""
    response = (
        get_client()
        .table("phases")
        .select("*")
        .eq("user_id", user_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


# --- Conversations -----------------------------------------------------

def create_conversation(user_id: str, role: str, content: str) -> dict:
    """Insert one conversation turn (role: 'user' or 'butler')."""
    payload = {
        "user_id": user_id,
        "role": role,
        "content": content,
        "created_at": utcnow_iso(),
    }
    response = get_client().table("conversations").insert(payload).execute()
    return response.data[0]


def get_recent_conversations(user_id: str, days: int = 30, limit: int = 200) -> list:
    """Return conversations from the last `days` days, oldest first."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    response = (
        get_client()
        .table("conversations")
        .select("*")
        .eq("user_id", user_id)
        .gte("created_at", since)
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return response.data


def get_last_conversation(user_id: str, role: Optional[str] = None) -> Optional[dict]:
    """Return the most recent conversation turn for a user, optionally filtered by role."""
    query = get_client().table("conversations").select("*").eq("user_id", user_id)
    if role:
        query = query.eq("role", role)
    response = query.order("created_at", desc=True).limit(1).execute()
    return response.data[0] if response.data else None


# --- Activity logs -------------------------------------------------------

def create_activity_log(user_id: str, event_type: str, metadata: Optional[dict] = None) -> dict:
    """Record one activity_logs event."""
    payload = {
        "user_id": user_id,
        "event_type": event_type,
        "metadata": metadata or {},
        "created_at": utcnow_iso(),
    }
    response = get_client().table("activity_logs").insert(payload).execute()
    return response.data[0]


def get_recent_activity(user_id: str, days: int = 30) -> list:
    """Return activity_logs from the last `days` days, oldest first."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    response = (
        get_client()
        .table("activity_logs")
        .select("*")
        .eq("user_id", user_id)
        .gte("created_at", since)
        .order("created_at")
        .execute()
    )
    return response.data


# --- Prompt improvements ---------------------------------------------------

def get_approved_prompt_improvements(limit: int = 10) -> list:
    """Return the most recent approved prompt_improvements rows.

    These are admin-approved, globally-applied butler behavior tweaks
    generated by the daily n8n improvement workflow — not per-user.
    """
    response = (
        get_client()
        .table("prompt_improvements")
        .select("*")
        .eq("approved", True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data
