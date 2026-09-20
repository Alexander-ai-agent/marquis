"""Marquis backend — Flask application and all 6 required API routes."""
from datetime import datetime, timezone

from flask import Flask, g, request
from flask_cors import CORS
from flask_limiter import Limiter

from agents.blocker_detector import run_blocker_detector
from agents.enhancement_suggester import run_enhancement_suggester
from agents.pathway_optimizer import run_pathway_optimizer
from agents.performance_analyst import run_performance_analyst
from auth_utils import (
    encode_token,
    extract_user_id_unverified,
    hash_password,
    is_valid_email,
    is_valid_password,
    require_auth,
    verify_password,
)
from butler import build_agent_context, build_system_prompt, get_butler_response
from config import Config
from responses import err
from supabase_client import (
    create_activity_log,
    create_conversation,
    create_user,
    days_since,
    get_active_phase,
    get_approved_prompt_improvements,
    get_last_conversation,
    get_phases,
    get_recent_activity,
    get_user_by_email,
)
from webhook_client import trigger_onboarding_webhook


def create_app() -> Flask:
    """Build and configure the Marquis Flask app."""
    Config.validate()

    app = Flask(__name__)

    CORS(
        app,
        origins=[Config.FRONTEND_URL],
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization"],
    )

    Limiter(
        key_func=lambda: extract_user_id_unverified() or request.remote_addr,
        app=app,
        default_limits=[Config.RATE_LIMIT],
    )

    # --- 1/2. Auth -----------------------------------------------------

    @app.route("/api/v1/marquis/auth/login", methods=["POST"])
    def login():
        data = request.get_json(silent=True) or {}
        email, password = data.get("email"), data.get("password")

        if not email or not password:
            return err("email and password are required.", 400)

        user = get_user_by_email(email)
        if not user or not user.get("password_hash") or not verify_password(password, user["password_hash"]):
            return err("Invalid email or password.", 401)

        token = encode_token(user["id"])
        return {"token": token, "user_id": user["id"], "name": user.get("name")}, 200

    @app.route("/api/v1/marquis/auth/signup", methods=["POST"])
    def signup():
        data = request.get_json(silent=True) or {}
        email, password, name = data.get("email"), data.get("password"), data.get("name")

        if not email or not password or not name:
            return err("email, password, and name are required.", 400)
        if not is_valid_email(email):
            return err("Enter a valid email address.", 400)
        if not is_valid_password(password):
            return err("Password must be at least 8 characters.", 400)
        if get_user_by_email(email):
            return err("An account with this email already exists.", 400)

        user = create_user(email=email, password_hash=hash_password(password), name=name)
        create_activity_log(user["id"], "user_signup", {})
        trigger_onboarding_webhook(user["id"], email, name)

        token = encode_token(user["id"])
        return {"token": token, "user_id": user["id"]}, 201

    # --- 6. User profile -----------------------------------------------

    @app.route("/api/v1/marquis/user/profile", methods=["GET"])
    @require_auth
    def user_profile():
        user = g.current_user
        return {
            "id": user["id"],
            "name": user.get("name"),
            "email": user.get("email"),
            "business_type": user.get("business_type"),
            "stage": user.get("stage"),
            "description": user.get("description"),
            "onboarding_complete": bool(user.get("onboarding_complete")),
            "created_at": user.get("created_at"),
        }, 200

    # --- 5. Phases -------------------------------------------------------

    @app.route("/api/v1/marquis/phases", methods=["GET"])
    @require_auth
    def phases():
        rows = get_phases(g.current_user["id"])
        result = []
        for phase in rows:
            result.append(
                {
                    "id": phase["id"],
                    "phase_number": phase["phase_number"],
                    "phase_name": phase["phase_name"],
                    "status": phase["status"],
                    "estimated_days": phase.get("estimated_days"),
                    "actual_days": phase.get("actual_days"),
                    "started_at": phase.get("started_at"),
                    "completed_at": phase.get("completed_at"),
                    "progress_percent": _progress_percent(phase),
                }
            )
        return {"phases": result}, 200

    # --- 4. Conversation -------------------------------------------------

    @app.route("/api/v1/marquis/conversation", methods=["POST"])
    @require_auth
    def conversation():
        data = request.get_json(silent=True) or {}
        message = data.get("message")
        history = data.get("conversation_history", [])

        if not message or not isinstance(message, str):
            return err("message is required.", 400)
        if len(message) > Config.MAX_MESSAGE_LENGTH:
            return err(f"message exceeds {Config.MAX_MESSAGE_LENGTH} characters.", 400)
        if not isinstance(history, list):
            return err("conversation_history must be a list.", 400)

        user = g.current_user
        user_id = user["id"]

        create_conversation(user_id, "user", message)

        performance = run_performance_analyst(user_id)
        pathway = run_pathway_optimizer(user_id)
        blocker = run_blocker_detector(user_id)
        enhancement = run_enhancement_suggester(user_id)
        agent_context = build_agent_context(performance, pathway, blocker, enhancement)

        prompt_improvements = get_approved_prompt_improvements()

        system_prompt = build_system_prompt(agent_context=agent_context, prompt_improvements=prompt_improvements)

        try:
            reply = get_butler_response(message, system_prompt, history)
        except Exception:
            return err("The butler is unavailable right now.", 500)

        create_conversation(user_id, "butler", reply)
        create_activity_log(user_id, "butler_interaction", {})

        return {"butler_response": reply, "agent_context": agent_context}, 200

    # --- 3. Dashboard ----------------------------------------------------

    @app.route("/api/v1/marquis/dashboard", methods=["GET"])
    @require_auth
    def dashboard():
        user = g.current_user
        user_id = user["id"]

        all_phases = get_phases(user_id)
        active_phase = next((p for p in all_phases if p["status"] == "active"), None)
        completed_count = sum(1 for p in all_phases if p["status"] == "complete")

        phase_ring = {
            "current_phase": active_phase["phase_number"] if active_phase else None,
            "total_phases": len(all_phases),
            "percent_complete": round((completed_count / len(all_phases)) * 100, 1) if all_phases else 0.0,
        }

        last_butler_msg = get_last_conversation(user_id, role="butler")
        last_butler_exchange = last_butler_msg["content"] if last_butler_msg else "No conversations yet."

        gap = None
        if active_phase and active_phase.get("estimated_days"):
            elapsed = round(days_since(active_phase.get("started_at")) or 0)
            gap = elapsed - active_phase["estimated_days"]

        week_activity = get_recent_activity(user_id, days=7)
        sessions_this_week = len({row["created_at"][:10] for row in week_activity})

        stat_cards = [
            {
                "label": "Current Phase",
                "value": active_phase["phase_name"] if active_phase else "Not started",
                "trend_arrow": "flat",
            },
            {
                "label": "Days On Phase",
                "value": (
                    f"{round(days_since(active_phase.get('started_at')) or 0)}/{active_phase.get('estimated_days')} days"
                    if active_phase and active_phase.get("estimated_days")
                    else "—"
                ),
                "trend_arrow": "up" if gap and gap > 0 else "down" if gap and gap < 0 else "flat",
            },
            {"label": "Sessions This Week", "value": sessions_this_week, "trend_arrow": "flat"},
            {
                "label": "Phases Complete",
                "value": f"{completed_count}/{len(all_phases)}" if all_phases else "0/0",
                "trend_arrow": "flat",
            },
        ]

        performance = run_performance_analyst(user_id)
        pathway = run_pathway_optimizer(user_id)
        blocker = run_blocker_detector(user_id)
        improvements = get_approved_prompt_improvements(limit=1)
        enhancement_text = (
            f"{improvements[0]['suggestion']} — {improvements[0].get('reason', '')}"
            if improvements
            else "Nothing new to surface this session."
        )

        agent_insights = [
            {"agent": "performance", "insight": performance.get("key_insight"), "type": "heartbeat"},
            {
                "agent": "pathway",
                "insight": pathway.get("recommendation") or f"Pathway is {pathway.get('pathway_status')}.",
                "type": "delta",
            },
            {
                "agent": "blocker",
                "insight": blocker.get("likely_cause") or "No blocker signals detected.",
                "type": "warning",
            },
            {"agent": "enhancement", "insight": enhancement_text, "type": "suggestion"},
        ]

        quick_links = [
            {
                "title": "Continue the conversation",
                "description": "Pick up where you left off with the butler.",
                "action": "conversation",
            },
            {
                "title": "View your pathway",
                "description": "See every phase and how your pace compares to estimate.",
                "action": "phases",
            },
            {
                "title": "Check your profile",
                "description": "Review your business details on file.",
                "action": "profile",
            },
        ]

        return {
            "greeting": _greeting(user.get("name")),
            "last_butler_exchange": last_butler_exchange,
            "phase_ring": phase_ring,
            "stat_cards": stat_cards,
            "agent_insights": agent_insights,
            "quick_links": quick_links,
        }, 200

    # --- Health (infra only, not part of the Marquis API surface) --------

    @app.route("/health", methods=["GET"])
    def health():
        return {"status": "ok"}, 200

    @app.errorhandler(404)
    def not_found(_e):
        return err("Resource not found.", 404)

    @app.errorhandler(500)
    def server_error(_e):
        return err("Something went wrong.", 500)

    return app


def _progress_percent(phase: dict):
    """(actual_days / estimated_days) * 100 once actual_days is known.

    For a phase still in progress (no actual_days yet), falls back to
    elapsed-days-so-far / estimated_days as a live proxy. Returns None
    when there's nothing to compute from (future phase, or no estimate).
    """
    estimated = phase.get("estimated_days")
    if not estimated:
        return None
    if phase.get("actual_days") is not None:
        return round((phase["actual_days"] / estimated) * 100, 1)
    if phase["status"] == "active" and phase.get("started_at"):
        elapsed = days_since(phase["started_at"]) or 0
        return round((elapsed / estimated) * 100, 1)
    return None


def _greeting(name: str) -> str:
    """Time-of-day greeting using the name, per the dashboard's greeting field."""
    hour = datetime.now(timezone.utc).hour
    if hour < 5:
        part = "Still up"
    elif hour < 12:
        part = "Good morning"
    elif hour < 17:
        part = "Good afternoon"
    elif hour < 21:
        part = "Good evening"
    else:
        part = "Good evening"
    return f"{part}, {name}." if name else f"{part}."


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)
