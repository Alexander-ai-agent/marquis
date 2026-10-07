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

def create_conversation(user_id: str, role: str, content: str,
                        exchange_id: Optional[str] = None, meta: Optional[dict] = None) -> dict:
    """Insert one conversation turn (role: 'user' or 'butler'). Both turns of
    an exchange share an exchange_id; `meta` keeps what Alfred attached."""
    payload = {
        "user_id": user_id,
        "role": role,
        "content": content,
        "created_at": utcnow_iso(),
    }
    if exchange_id:
        payload["exchange_id"] = exchange_id
    if meta:
        payload["meta"] = meta
    response = get_client().table("conversations").insert(payload).execute()
    return response.data[0]


def get_conversation_rows(user_id: str, limit: int, before: Optional[str] = None) -> list:
    """Newest-first conversation rows for this user only, optionally older than `before` (ISO time)."""
    query = get_client().table("conversations").select("*").eq("user_id", user_id)
    if before:
        query = query.lt("created_at", before)
    return query.order("created_at", desc=True).limit(limit).execute().data or []


def count_user_messages(user_id: str) -> int:
    """How many questions this user has asked (one per exchange)."""
    res = (get_client().table("conversations").select("id", count="exact")
           .eq("user_id", user_id).eq("role", "user").execute())
    return res.count or 0


# --- Canvas items (creations) ----------------------------------------------

_ITEM_HEADER_COLUMNS = "id,exchange_id,kind,title,x,y,w,h,dismissed,created_at,updated_at"


def create_canvas_item(user_id: str, kind: str, title: str, spec: dict, exchange_id: Optional[str] = None) -> dict:
    row = {"user_id": user_id, "kind": kind, "title": title, "spec": spec}
    if exchange_id:
        row["exchange_id"] = exchange_id
    return get_client().table("canvas_items").insert(row).execute().data[0]


def list_canvas_headers(user_id: str, limit: int, before: Optional[str] = None) -> list:
    """Newest-first headers (no spec) of this user's undismissed items."""
    query = (get_client().table("canvas_items").select(_ITEM_HEADER_COLUMNS)
             .eq("user_id", user_id).eq("dismissed", False))
    if before:
        query = query.lt("created_at", before)
    return query.order("created_at", desc=True).limit(limit).execute().data or []


def get_canvas_item(user_id: str, item_id: str) -> Optional[dict]:
    res = (get_client().table("canvas_items").select("*")
           .eq("user_id", user_id).eq("id", item_id).limit(1).execute())
    return res.data[0] if res.data else None


def update_canvas_item(user_id: str, item_id: str, fields: dict) -> Optional[dict]:
    res = (get_client().table("canvas_items").update({**fields, "updated_at": utcnow_iso()})
           .eq("user_id", user_id).eq("id", item_id).execute())
    return res.data[0] if res.data else None


def delete_canvas_item(user_id: str, item_id: str) -> bool:
    res = get_client().table("canvas_items").delete().eq("user_id", user_id).eq("id", item_id).execute()
    return bool(res.data)


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


# --- Writes used by onboarding + phase completion ---------------------------

def update_user(user_id: str, fields: dict) -> Optional[dict]:
    """Update profile columns on a user row (business_type, stage, description, onboarding_complete)."""
    response = get_client().table("users").update(fields).eq("id", user_id).execute()
    return response.data[0] if response.data else None


def get_phase_by_id(user_id: str, phase_id: str) -> Optional[dict]:
    """Return one phase, scoped to its owner (never another user's row)."""
    response = (
        get_client()
        .table("phases")
        .select("*")
        .eq("id", phase_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def create_phase(user_id: str, phase_number: int, phase_name: str, status: str, estimated_days: Optional[int]) -> dict:
    """Insert a phase row. Status is 'future' | 'active' | 'complete'."""
    payload = {
        "user_id": user_id,
        "phase_number": phase_number,
        "phase_name": phase_name,
        "status": status,
        "estimated_days": estimated_days,
        "started_at": utcnow_iso() if status == "active" else None,
    }
    response = get_client().table("phases").insert(payload).execute()
    return response.data[0]


def update_phase(user_id: str, phase_id: str, fields: dict) -> Optional[dict]:
    """Update a phase row, scoped to its owner."""
    response = get_client().table("phases").update(fields).eq("id", phase_id).eq("user_id", user_id).execute()
    return response.data[0] if response.data else None
