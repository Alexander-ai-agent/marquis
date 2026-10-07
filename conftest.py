"""Shared pytest fixtures: env setup, an in-memory fake Supabase, and a
Flask test client wired to use it instead of the real network.

Required env vars are set here, before `app` (and therefore `config`) is
ever imported — Config reads them at class-definition time, so this must
happen first. pytest always loads the rootdir's conftest.py before
collecting test modules, which is what makes that ordering guaranteed.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

FRONTEND_URL = "https://marquis-frontend.example.com"

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-service-role-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("FRONTEND_URL", FRONTEND_URL)

import jwt
import pytest

import app as app_module
import auth_utils
import supabase_client
from agents import blocker_detector, enhancement_suggester, pathway_optimizer, performance_analyst
from auth_utils import encode_token, hash_password
from config import Config


class FakeDB:
    """In-memory stand-in for supabase_client's functions.

    Method names and signatures mirror supabase_client.py exactly, so it
    can be swapped in via monkeypatch without touching call sites.
    """

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.phases: dict[str, dict] = {}
        self.conversations: list[dict] = []
        self.activity_logs: list[dict] = []
        self.prompt_improvements: list[dict] = []
        self.canvas_items: list[dict] = []
        self._seq = 0
        self._base = datetime.now(timezone.utc)

    # --- users ---
    def create_user(self, email, password_hash, name):
        user_id = str(uuid.uuid4())
        row = {
            "id": user_id,
            "email": email,
            "password_hash": password_hash,
            "name": name,
            "business_type": None,
            "stage": None,
            "description": None,
            "onboarding_complete": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.users[user_id] = row
        return row

    def get_user_by_email(self, email):
        return next((u for u in self.users.values() if u["email"] == email), None)

    def get_user_by_id(self, user_id):
        return self.users.get(user_id)

    # --- phases ---
    def get_phases(self, user_id):
        rows = [p for p in self.phases.values() if p["user_id"] == user_id]
        return sorted(rows, key=lambda p: p["phase_number"])

    def get_active_phase(self, user_id):
        return next(
            (p for p in self.phases.values() if p["user_id"] == user_id and p["status"] == "active"),
            None,
        )

    def add_phase(self, user_id, **overrides):
        """Test helper (not part of supabase_client's real interface)."""
        phase_id = str(uuid.uuid4())
        row = {
            "id": phase_id,
            "user_id": user_id,
            "phase_number": 1,
            "phase_name": "Foundation",
            "status": "active",
            "estimated_days": 14,
            "actual_days": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        row.update(overrides)
        self.phases[phase_id] = row
        return row

    # --- conversations ---
    def _stamp(self):
        """Strictly increasing timestamps, so ordering never ties in a fast test."""
        self._seq += 1
        return (self._base + timedelta(microseconds=self._seq)).isoformat()

    def create_conversation(self, user_id, role, content, exchange_id=None, meta=None):
        row = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "role": role,
            "content": content,
            "exchange_id": exchange_id,
            "meta": meta or {},
            "created_at": self._stamp(),
        }
        self.conversations.append(row)
        return row

    def get_conversation_rows(self, user_id, limit, before=None):
        rows = [c for c in self.conversations if c["user_id"] == user_id and (before is None or c["created_at"] < before)]
        return sorted(rows, key=lambda c: c["created_at"], reverse=True)[:limit]

    def count_user_messages(self, user_id):
        return sum(1 for c in self.conversations if c["user_id"] == user_id and c["role"] == "user")

    # --- canvas items ---
    def create_canvas_item(self, user_id, kind, title, spec, exchange_id=None):
        row = {"id": str(uuid.uuid4()), "user_id": user_id, "exchange_id": exchange_id, "kind": kind, "title": title,
               "spec": spec, "x": None, "y": None, "w": None, "h": None, "dismissed": False,
               "dismissed_at": None, "created_at": self._stamp(), "updated_at": self._stamp()}
        self.canvas_items.append(row)
        return dict(row)

    def list_canvas_headers(self, user_id, limit, before=None):
        rows = [i for i in self.canvas_items if i["user_id"] == user_id and not i["dismissed"]
                and (before is None or i["created_at"] < before)]
        rows = sorted(rows, key=lambda i: i["created_at"], reverse=True)[:limit]
        return [{k: v for k, v in r.items() if k not in ("spec", "user_id", "dismissed_at")} for r in rows]

    def get_canvas_item(self, user_id, item_id):
        return next((dict(i) for i in self.canvas_items if i["user_id"] == user_id and i["id"] == item_id), None)

    def update_canvas_item(self, user_id, item_id, fields):
        for i in self.canvas_items:
            if i["user_id"] == user_id and i["id"] == item_id:
                i.update(fields)
                i["updated_at"] = self._stamp()
                return dict(i)
        return None

    def delete_canvas_item(self, user_id, item_id):
        before = len(self.canvas_items)
        self.canvas_items = [i for i in self.canvas_items if not (i["user_id"] == user_id and i["id"] == item_id)]
        return len(self.canvas_items) < before

    def get_recent_conversations(self, user_id, days=30, limit=200):
        rows = [c for c in self.conversations if c["user_id"] == user_id]
        return rows[:limit]

    def get_last_conversation(self, user_id, role=None):
        rows = [c for c in self.conversations if c["user_id"] == user_id and (role is None or c["role"] == role)]
        if not rows:
            return None
        return sorted(rows, key=lambda c: c["created_at"])[-1]

    # --- activity logs ---
    def create_activity_log(self, user_id, event_type, metadata=None):
        row = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "event_type": event_type,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.activity_logs.append(row)
        return row

    def get_recent_activity(self, user_id, days=30):
        return [a for a in self.activity_logs if a["user_id"] == user_id]

    # --- writes used by onboarding + phase completion ---
    def update_user(self, user_id, fields):
        if user_id in self.users:
            self.users[user_id].update(fields)
            return self.users[user_id]
        return None

    def get_phase_by_id(self, user_id, phase_id):
        p = self.phases.get(phase_id)
        return p if p and p["user_id"] == user_id else None

    def create_phase(self, user_id, phase_number, phase_name, status, estimated_days):
        return self.add_phase(user_id, phase_number=phase_number, phase_name=phase_name, status=status,
                              estimated_days=estimated_days,
                              started_at=datetime.now(timezone.utc).isoformat() if status == "active" else None)

    def update_phase(self, user_id, phase_id, fields):
        p = self.get_phase_by_id(user_id, phase_id)
        if not p:
            return None
        p.update(fields)
        return p

    # --- prompt improvements ---
    def get_approved_prompt_improvements(self, limit=10):
        return [p for p in self.prompt_improvements if p.get("approved")][:limit]


# Every (module, [function names]) pair that imported a supabase_client
# function by name via `from supabase_client import ...` — patching
# supabase_client.<name> alone would not affect these already-bound refs.
_PATCH_TARGETS = {
    supabase_client: [
        "create_user", "get_user_by_email", "get_user_by_id", "get_phases", "get_active_phase",
        "create_conversation", "get_recent_conversations", "get_last_conversation",
        "create_activity_log", "get_recent_activity", "get_approved_prompt_improvements",
        "update_user", "get_phase_by_id", "create_phase", "update_phase",
        "get_conversation_rows", "count_user_messages", "create_canvas_item", "list_canvas_headers",
        "get_canvas_item", "update_canvas_item", "delete_canvas_item",
    ],
    app_module: [
        "create_activity_log", "create_conversation", "create_user", "get_active_phase",
        "get_approved_prompt_improvements", "get_last_conversation", "get_phases",
        "get_recent_activity", "get_user_by_email",
        "update_user", "get_phase_by_id", "create_phase", "update_phase", "get_recent_conversations",
        "get_conversation_rows", "count_user_messages", "create_canvas_item", "list_canvas_headers",
        "get_canvas_item", "update_canvas_item", "delete_canvas_item",
    ],
    auth_utils: ["get_user_by_id"],
    performance_analyst: ["get_active_phase", "get_recent_activity", "get_recent_conversations"],
    pathway_optimizer: ["get_phases"],
    blocker_detector: ["get_recent_activity", "get_recent_conversations"],
    enhancement_suggester: ["get_recent_conversations", "get_user_by_id", "get_active_phase"],
}


@pytest.fixture
def fake_db(monkeypatch):
    """A fresh FakeDB per test, patched in everywhere supabase_client is used."""
    db = FakeDB()
    for module, names in _PATCH_TARGETS.items():
        for name in names:
            monkeypatch.setattr(module, name, getattr(db, name))
    return db


@pytest.fixture
def mock_claude(monkeypatch):
    """Patch the Claude call so /conversation never hits the real Anthropic API.

    Returns a mutable dict so tests can override the reply text or force
    an exception (`mock_claude["side_effect"] = RuntimeError(...)`).
    """
    state = {"reply": "Three things require your attention.", "side_effect": None}

    def fake_get_butler_response(message, system_prompt, history=None):
        if state["side_effect"]:
            raise state["side_effect"]
        return state["reply"]

    monkeypatch.setattr(app_module, "get_butler_response", fake_get_butler_response)
    return state


@pytest.fixture
def client(fake_db):
    """Flask test client with rate limiting disabled (irrelevant to correctness here)."""
    app_module.app.config["TESTING"] = True
    app_module.app.config["RATELIMIT_ENABLED"] = False
    return app_module.app.test_client()


@pytest.fixture
def make_user(fake_db):
    """Seed a user with a known plaintext password and return (user_row, plaintext_password)."""

    def _make(email="founder@example.com", password="correct-horse-battery", name="Ada Founder"):
        user = fake_db.create_user(email=email, password_hash=hash_password(password), name=name)
        return user, password

    return _make


@pytest.fixture
def auth_headers(make_user):
    """A ready-to-use (user, headers) pair with a valid Bearer token."""
    user, _ = make_user()
    token = encode_token(user["id"])
    return user, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_expired_token():
    """Factory fixture: build a JWT for a given user_id that's already expired."""
    import datetime as dt

    def _make(user_id: str) -> str:
        payload = {
            "sub": user_id,
            "iat": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10),
            "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3),
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)

    return _make
