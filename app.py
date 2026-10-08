"""Marquis backend — Flask application: the 6 original API routes plus onboarding
(questions, pathway) and phase completion added for the Sep 24 frontend redesign."""
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, Response, g, request
from flask_cors import CORS
from flask_limiter import Limiter

from agents.blocker_detector import _keywords, run_blocker_detector
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
import canvas_items as canvas_items_mod
import history as history_mod
from butler import (
    answer_from_web,
    build_agent_context,
    build_system_prompt,
    generate_clarifying_questions,
    generate_pathway,
    get_butler_response,
    interpret_drawing,
    parse_visualization,
    parse_web_request,
    split_build_request,
    split_design_request,
    REPLY_STYLES,
)
from design import design_canvas
from site_builder import build_site
from specialists import store
from specialists.briefing import alfred_context, my_agents
from specialists.catalog import (
    BUSINESS_TYPE_CATEGORY,
    CATALOG,
    CATEGORIES,
    LIMITS,
    entry as catalog_entry,
    public as catalog_public,
)
from specialists.engines import CapReached, run_agent, validate_settings
from specialists.safety import guard_reply

# Scheduled agents run per cron call (every 30 min). Kept small so one call
# finishes well inside the worker timeout.
RUN_DUE_BATCH = 6
from images import search_images
from voice import synthesize
from web import research
from config import Config
from enhancement_library import get_enhancements
from responses import err
from supabase_client import (
    create_activity_log,
    count_user_messages,
    create_canvas_item,
    create_conversation,
    delete_canvas_item,
    get_canvas_item,
    get_conversation_rows,
    list_canvas_headers,
    update_canvas_item,
    create_phase,
    create_user,
    days_since,
    get_active_phase,
    get_approved_prompt_improvements,
    get_last_conversation,
    get_phase_by_id,
    get_phases,
    get_recent_activity,
    get_recent_conversations,
    get_user_by_email,
    update_phase,
    update_user,
)
from webhook_client import notify_phase_complete, trigger_onboarding_webhook


def create_app() -> Flask:
    """Build and configure the Marquis Flask app."""
    Config.validate()

    app = Flask(__name__)

    CORS(
        app,
        origins=[Config.FRONTEND_URL, *Config.CORS_EXTRA_ORIGINS],
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

        try:
            user = get_user_by_email(email)
        except Exception as e:
            print(f"[auth/login] Supabase lookup failed: {e}")
            return err("The database is unavailable right now. Try again shortly.", 503)

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

        try:
            existing = get_user_by_email(email)
        except Exception as e:
            print(f"[auth/signup] Supabase lookup failed: {e}")
            return err("The database is unavailable right now. Try again shortly.", 503)
        if existing:
            return err("An account with this email already exists.", 400)

        try:
            user = create_user(email=email, password_hash=hash_password(password), name=name)
        except Exception as e:
            print(f"[auth/signup] Supabase insert failed: {e}")
            return err("Could not create your account right now. Try again shortly.", 503)

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
            "butler_name": user.get("butler_name") or "Reeves",
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

        if not message or not isinstance(message, str):
            return err("message is required.", 400)
        if len(message) > Config.MAX_MESSAGE_LENGTH:
            return err(f"message exceeds {Config.MAX_MESSAGE_LENGTH} characters.", 400)
        # Older clients still send their own history. It is accepted and ignored:
        # the model's memory comes from the database, so it survives a refresh.
        if "conversation_history" in data and not isinstance(data["conversation_history"], list):
            return err("conversation_history must be a list.", 400)
        reply_style = data.get("reply_style") if data.get("reply_style") in REPLY_STYLES else "brief"

        user = g.current_user
        user_id = user["id"]

        exchange_id = str(uuid.uuid4())
        history = history_mod.model_messages(list(reversed(get_conversation_rows(user_id, MODEL_HISTORY_ROWS))))
        create_conversation(user_id, "user", message, exchange_id=exchange_id)

        performance = run_performance_analyst(user_id)
        pathway = run_pathway_optimizer(user_id)
        blocker = run_blocker_detector(user_id)
        enhancement = run_enhancement_suggester(user_id)
        agent_context = build_agent_context(performance, pathway, blocker, enhancement)
        try:
            specialists, mock_figs, real_figs = alfred_context(user_id)
        except Exception as e:  # specialists are additive; the core four still answer
            print(f"[conversation] specialist context unavailable: {e}")
            specialists, mock_figs, real_figs = "", set(), set()
        if specialists:
            agent_context["specialists"] = specialists

        prompt_improvements = get_approved_prompt_improvements()

        system_prompt = build_system_prompt(
            agent_context=agent_context, prompt_improvements=prompt_improvements, business_type=user.get("business_type"),
            web_enabled=bool(Config.TAVILY_API_KEY), reply_style=reply_style,
        )

        try:
            raw_reply = get_butler_response(message, system_prompt, history)
        except Exception:
            return err("The butler is unavailable right now.", 500)

        # The butler decides when it needs the web: a <<WEB query>> reply
        # means "read the pages, then answer from them".
        sources = []
        web_query = parse_web_request(raw_reply) if Config.TAVILY_API_KEY else None
        design = build = None
        if web_query:
            reply, visualization, sources = _answer_with_web(message, web_query, reply_style)
        else:
            # A <<DESIGN brief>> line hands the work to the designer and a
            # <<BUILD brief>> line to the builder; the frontend fetches the
            # result (/canvas/design, /canvas/site) while the butler speaks.
            prose, brief = split_design_request(raw_reply)
            prose, build_brief = split_build_request(prose)
            if brief:
                design = {"brief": brief}
            elif build_brief:
                build = {"brief": build_brief}
            # Living Canvas: split prose from the optional visualization payload.
            # Fail-soft — any problem yields visualization=None, never an error.
            reply, visualization = parse_visualization(prose)
            if design or build:
                visualization = None
                reply = reply or ("Allow me a moment at the drafting table." if design else "I'll have it built for you.")
        if visualization and visualization["type"] == "phase_timeline":
            visualization["phases"] = _timeline_phases(get_phases(user_id))
            if not visualization["phases"]:
                visualization = None

        # Alfred keeps the specialists' rules: mock figures are always called
        # mock, and a trading user never receives a trade recommendation.
        if not reply.strip() and (visualization or sources):
            reply = "Here it is, sir." if visualization else "Here is what I read, sir."   # a creation alone is still spoken for
        reply = guard_reply(reply, user.get("business_type"), mock_figs, real_figs)

        create_conversation(user_id, "butler", reply, exchange_id=exchange_id,
                            meta={"sources": sources} if sources else None)
        create_activity_log(user_id, "butler_interaction", {})
        items = _save_exchange_items(user_id, exchange_id, visualization, sources)

        return {"butler_response": reply, "agent_context": agent_context, "visualization": visualization,
                "sources": sources, "design": design, "build": build,
                "exchange_id": exchange_id, "canvas_items": items}, 200

    # --- Canvas: read the user's drawing, find images ----------------------

    @app.route("/api/v1/marquis/canvas/interpret", methods=["POST"])
    @require_auth
    def canvas_interpret():
        """The user drew first: turn their sketch into a blueprint."""
        data = request.get_json(silent=True) or {}
        image = data.get("image")
        hint = data.get("hint") or ""
        if not isinstance(image, str) or not image:
            return err("image is required.", 400)
        if image.startswith("data:"):
            head, _, image = image.partition(",")
            if "image/png" not in head:
                return err("image must be a PNG.", 400)
        if len(image) > Config.MAX_DRAWING_BYTES:
            return err("That drawing is too large.", 413)
        if not isinstance(hint, str):
            return err("hint must be text.", 400)
        try:
            understood, blueprint = interpret_drawing(image, hint)
        except Exception as e:
            print(f"[canvas/interpret] failed: {e}")
            return err("The butler is unavailable right now.", 502)
        if not blueprint:
            return err("I couldn't make out a layout in that drawing.", 422)
        create_activity_log(g.current_user["id"], "drawing_interpreted", {"regions": len(blueprint["regions"])})
        item = _save_item(g.current_user["id"], "blueprint", blueprint.get("title"), blueprint, _exchange_arg(data))
        return {"butler_response": understood or "Here is what I read.", "visualization": blueprint, "item": item}, 200

    @app.route("/api/v1/marquis/images/search", methods=["GET"])
    @require_auth
    def images_search():
        q = (request.args.get("q") or "").strip()
        if not q or len(q) > 60:
            return err("q is required (max 60 characters).", 400)
        if not Config.UNSPLASH_ACCESS_KEY:
            return err("Image search isn't configured.", 503)
        try:
            return {"images": search_images(q)}, 200
        except Exception as e:
            print(f"[images/search] failed: {e}")
            return err("Image search is unavailable right now.", 502)

    # --- Specialist agents ------------------------------------------------

    @app.route("/api/v1/marquis/agents/catalog", methods=["GET"])
    @require_auth
    def agents_catalog():
        """Every specialist on offer, grouped by category, plus the user's own category."""
        suggested = BUSINESS_TYPE_CATEGORY.get(g.current_user.get("business_type") or "", "other")
        return {
            "categories": CATEGORIES,
            "suggested_category": suggested,
            "agents": [catalog_public(a) for a in CATALOG.values()],
            "limits": {k: LIMITS[k] for k in ("enabled_specialists", "min_interval_hours", "max_interval_hours")},
        }, 200

    @app.route("/api/v1/marquis/agents/mine", methods=["GET"])
    @require_auth
    def agents_mine():
        return {"agents": my_agents(g.current_user["id"])}, 200

    @app.route("/api/v1/marquis/agents/mine", methods=["POST"])
    @require_auth
    def agents_enable():
        """Enable a specialist (or update it if already enabled)."""
        data = request.get_json(silent=True) or {}
        user_id = g.current_user["id"]
        entry_ = catalog_entry(data.get("agent_key") or "")
        if not entry_:
            return err("Unknown agent.", 400)
        current = store.list_user_agents(user_id)
        if not any(u["agent_key"] == entry_["key"] for u in current) and len(current) >= LIMITS["enabled_specialists"]:
            return err(f"You can enable up to {LIMITS['enabled_specialists']} specialists.", 400)
        # enabled:false = chosen (e.g. at onboarding) but not set up yet: no
        # settings required, and it never runs until enabled with settings.
        enabled = data.get("enabled") is not False
        fields, problem = _agent_fields(entry_, data, require_settings=enabled)
        if problem:
            return err(problem, 400)
        if not enabled:
            fields.pop("next_run_at", None)
        row = store.upsert_user_agent(user_id, entry_["key"], {**fields, "enabled": enabled, "state": "IDLE"})
        create_activity_log(user_id, "agent_enabled", {"agent": entry_["key"]})
        return {"agent": row}, 201

    @app.route("/api/v1/marquis/agents/mine/<agent_id>", methods=["PATCH"])
    @require_auth
    def agents_update(agent_id):
        ua = store.get_user_agent(g.current_user["id"], agent_id)
        if not ua:
            return err("Resource not found.", 404)
        data = request.get_json(silent=True) or {}
        fields, problem = _agent_fields(catalog_entry(ua["agent_key"]), data, require_settings=False, current=ua)
        if problem:
            return err(problem, 400)
        if isinstance(data.get("enabled"), bool):
            if data["enabled"] and not ua["enabled"] and "settings" not in fields:
                settings, problem = validate_settings(catalog_entry(ua["agent_key"]), ua.get("settings"))
                if problem:
                    return err(f"Finish setting it up first: {problem}", 400)
            fields["enabled"] = data["enabled"]
            if data["enabled"] and fields.get("run_mode") == "scheduled":
                fields["next_run_at"] = datetime.now(timezone.utc).isoformat()
        return {"agent": store.update_user_agent(ua["id"], fields)}, 200

    @app.route("/api/v1/marquis/agents/mine/<agent_id>", methods=["DELETE"])
    @require_auth
    def agents_remove(agent_id):
        if not store.get_user_agent(g.current_user["id"], agent_id):
            return err("Resource not found.", 404)
        store.delete_user_agent(g.current_user["id"], agent_id)
        return {"removed": agent_id}, 200

    @app.route("/api/v1/marquis/agents/mine/<agent_id>/run", methods=["POST"])
    @require_auth
    def agents_run(agent_id):
        """Run a specialist now (on demand)."""
        user_id = g.current_user["id"]
        ua = store.get_user_agent(user_id, agent_id)
        if not ua or not ua["enabled"]:
            return err("Resource not found.", 404)
        req = (request.get_json(silent=True) or {}).get("request") or ""
        if not isinstance(req, str) or len(req) > 500:
            return err("request must be text (max 500 characters).", 400)
        try:
            run = run_agent(user_id, ua, "manual", request=req)
        except CapReached as e:
            return err(str(e), 429)
        return {"run": run}, 200

    @app.route("/api/v1/marquis/agents/mine/<agent_id>/entries", methods=["POST"])
    @require_auth
    def agents_add_entry(agent_id):
        """Log a figure (Tracker) or an entry (Reviewer)."""
        user_id = g.current_user["id"]
        ua = store.get_user_agent(user_id, agent_id)
        entry_ = catalog_entry(ua["agent_key"]) if ua else None
        if not entry_ or entry_["archetype"] not in ("tracker", "reviewer"):
            return err("This agent doesn't take entries.", 400 if ua else 404)
        data = request.get_json(silent=True) or {}
        row = {"user_agent_id": ua["id"], "user_id": user_id}
        if entry_["archetype"] == "tracker":
            value = data.get("value")
            if not isinstance(data.get("metric"), str) or not data["metric"].strip() \
                    or not isinstance(value, (int, float)) or isinstance(value, bool):
                return err("metric (text) and value (number) are required.", 400)
            row.update(kind="metric", metric=data["metric"].strip()[:40], value=value,
                       body={"mock": True} if data.get("mock") is True else {})
        else:
            body = data.get("body")
            if not isinstance(body, dict) or not body or len(json.dumps(body)) > 4000:
                return err("body must be a non-empty object (max 4000 characters).", 400)
            row.update(kind="log", body={**body, **({"mock": True} if data.get("mock") is True else {})})
        if isinstance(data.get("occurred_at"), str):
            row["occurred_at"] = data["occurred_at"][:40]
        stored = store.add_entry(row)
        result = {"entry": stored}
        if entry_["archetype"] == "tracker":
            # Trackers check limits whenever a figure arrives (cheap: no model unless flagged).
            try:
                result["run"] = run_agent(user_id, ua, "manual")
            except CapReached as e:
                result["note"] = str(e)
        return result, 201

    @app.route("/internal/agents/run-due", methods=["POST"])
    def agents_run_due():
        """Called by the Railway cron service every 30 minutes. Requires the
        shared secret; anything else is refused before any work happens."""
        supplied = request.headers.get("X-Cron-Secret", "")
        if not Config.CRON_SECRET or not hmac.compare_digest(supplied.encode(), Config.CRON_SECRET.encode()):
            return err("Not found.", 404)
        claimed = store.claim_due_agents(RUN_DUE_BATCH)
        done, capped, failed = 0, 0, 0
        for ua in claimed:
            try:
                run_agent(ua["user_id"], ua, "schedule")
                done += 1
            except CapReached:
                capped += 1
            except Exception as e:
                failed += 1
                print(f"[run-due] {ua.get('agent_key')} failed: {e}")
        return {"claimed": len(claimed), "ran": done, "capped": capped, "failed": failed}, 200

    @app.route("/api/v1/marquis/canvas/design", methods=["POST"])
    @require_auth
    def canvas_design():
        """The designer realises a brief the butler handed over."""
        data = request.get_json(silent=True) or {}
        brief = data.get("brief")
        if not isinstance(brief, str) or not brief.strip() or len(brief) > 400:
            return err("brief is required (max 400 characters).", 400)
        try:
            said, design = design_canvas(brief.strip())
        except Exception as e:
            print(f"[canvas/design] failed: {e}")
            return err("The designer is unavailable right now.", 502)
        if not design:
            return err("The designer couldn't produce a usable composition.", 422)
        create_activity_log(g.current_user["id"], "design_drafted", {"variants": len(design["variants"])})
        item = _save_item(g.current_user["id"], "design", design.get("title"), design, _exchange_arg(data))
        return {"butler_response": said, "visualization": design, "item": item}, 200

    @app.route("/api/v1/marquis/canvas/site", methods=["POST"])
    @require_auth
    def canvas_site():
        """The builder makes the page the butler handed over."""
        data = request.get_json(silent=True) or {}
        brief = data.get("brief")
        if not isinstance(brief, str) or not brief.strip() or len(brief) > 500:
            return err("brief is required (max 500 characters).", 400)
        try:
            said, site = build_site(brief.strip())
        except Exception as e:
            print(f"[canvas/site] failed: {e}")
            return err("The builder is unavailable right now.", 502)
        if not site:
            return err("The builder couldn't produce a working page.", 422)
        create_activity_log(g.current_user["id"], "site_built", {"bytes": len(site["html"])})
        item = _save_item(g.current_user["id"], "site", site.get("title"), site, _exchange_arg(data))
        return {"butler_response": said, "visualization": site, "item": item}, 200

    # --- History and canvas items (persistence) ---------------------------

    @app.route("/api/v1/marquis/history", methods=["GET"])
    @require_auth
    def history_get():
        """This user's recent exchanges, newest first, paginated by `before`."""
        user_id = g.current_user["id"]
        limit = _int_arg("limit", history_mod.DEFAULT_PAGE, 1, history_mod.MAX_PAGE)
        before, bad = _time_arg("before")
        if bad:
            return err("before must be an ISO timestamp.", 400)
        out = history_mod.page(get_conversation_rows(user_id, limit * 2 + 1, before), limit)
        out["total"] = count_user_messages(user_id)
        return out, 200

    @app.route("/api/v1/marquis/canvas/items", methods=["GET"])
    @require_auth
    def canvas_items_list():
        """Headers of this user's undismissed items, newest first. Only the
        newest comes with its full spec; the rest load when expanded."""
        user_id = g.current_user["id"]
        limit = _int_arg("limit", 30, 1, 50)
        before, bad = _time_arg("before")
        if bad:
            return err("before must be an ISO timestamp.", 400)
        rows = list_canvas_headers(user_id, limit + 1, before)
        has_more = len(rows) > limit
        rows = rows[:limit]
        newest = None
        if rows and not before:
            full = get_canvas_item(user_id, rows[0]["id"])
            newest = _live_item(user_id, full) if full else None
        return {"items": [canvas_items_mod.public(r, with_spec=False) for r in rows], "newest": newest,
                "has_more": has_more, "next_before": rows[-1]["created_at"] if has_more and rows else None}, 200

    @app.route("/api/v1/marquis/canvas/items/<item_id>", methods=["GET"])
    @require_auth
    def canvas_item_get(item_id):
        row = get_canvas_item(g.current_user["id"], item_id) if canvas_items_mod.is_uuid(item_id) else None
        if not row:
            return err("Resource not found.", 404)
        return {"item": _live_item(g.current_user["id"], row)}, 200

    @app.route("/api/v1/marquis/canvas/items/<item_id>", methods=["PATCH"])
    @require_auth
    def canvas_item_update(item_id):
        """The user's own changes: edit in place, dismiss, or move/resize."""
        user_id = g.current_user["id"]
        row = get_canvas_item(user_id, item_id) if canvas_items_mod.is_uuid(item_id) else None
        if not row:
            return err("Resource not found.", 404)
        data = request.get_json(silent=True) or {}
        fields = {}
        if "spec" in data:
            if row["kind"] not in canvas_items_mod.EDITABLE_KINDS:
                return err("This item cannot be edited.", 400)
            clean = canvas_items_mod.clean_spec(row["kind"], data["spec"])
            if not clean:
                return err("That is not a valid " + row["kind"] + ".", 400)
            if "sources" not in clean and (row.get("spec") or {}).get("sources"):
                clean["sources"] = row["spec"]["sources"]
            fields["spec"] = clean
        if "dismissed" in data:
            if not isinstance(data["dismissed"], bool):
                return err("dismissed must be true or false.", 400)
            fields["dismissed"] = data["dismissed"]
            fields["dismissed_at"] = datetime.now(timezone.utc).isoformat() if data["dismissed"] else None
        for key in ("x", "y", "w", "h"):
            if key in data:
                if not isinstance(data[key], (int, float)) or isinstance(data[key], bool):
                    return err(f"{key} must be a number.", 400)
                fields[key] = float(data[key])
        if not fields:
            return err("Nothing to change.", 400)
        updated = update_canvas_item(user_id, item_id, fields)
        return {"item": canvas_items_mod.public(updated, with_spec=False)}, 200

    @app.route("/api/v1/marquis/canvas/items/<item_id>", methods=["DELETE"])
    @require_auth
    def canvas_item_delete(item_id):
        if not canvas_items_mod.is_uuid(item_id) or not delete_canvas_item(g.current_user["id"], item_id):
            return err("Resource not found.", 404)
        return {"deleted": item_id}, 200

    @app.route("/api/v1/marquis/voice", methods=["POST"])
    @require_auth
    def voice():
        """Alfred's line as speech (Fish Audio MP3)."""
        text = (request.get_json(silent=True) or {}).get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > Config.MAX_SPEECH_CHARS:
            return err(f"text is required (max {Config.MAX_SPEECH_CHARS} characters).", 400)
        if not Config.FISH_AUDIO_API_KEY:
            return err("Voice isn't configured.", 503)
        try:
            audio = synthesize(text.strip())
        except Exception as e:
            print(f"[voice] synthesis failed: {e}")
            return err("The voice is unavailable right now.", 502)
        return Response(audio, mimetype="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.route("/api/v1/marquis/research", methods=["POST"])
    @require_auth
    def web_research():
        """Search the web (Tavily), read the top pages (Scrapling), answer with citations."""
        data = request.get_json(silent=True) or {}
        query = data.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            return err("query is required (max 300 characters).", 400)
        if not Config.TAVILY_API_KEY:
            return err("Web research isn't configured.", 503)
        query = query.strip()
        try:
            sources = research(query)
        except Exception as e:
            print(f"[research] search failed: {e}")
            return err("Web search is unavailable right now.", 502)
        if not sources:
            return {"butler_response": "I found nothing on the web that answers that.",
                    "visualization": None, "sources": []}, 200
        try:
            text, viz = answer_from_web(query, sources)
        except Exception as e:
            print(f"[research] answer failed: {e}")
            return err("The butler is unavailable right now.", 502)
        create_activity_log(g.current_user["id"], "web_research", {"sources": len(sources)})
        return {
            "butler_response": text,
            "visualization": viz,
            "sources": _public_sources(sources),
        }, 200

    # --- Agent signals: real data for the canvas's idle presences ----------

    @app.route("/api/v1/marquis/agents/signals", methods=["GET"])
    @require_auth
    def agent_signals():
        """What each agent is actually reading — so the idle canvas never
        shows invented data to a real user. Empty lists mean "nothing to
        read yet", which the frontend renders as an honest quiet state."""
        user = g.current_user
        user_id = user["id"]
        today = datetime.now(timezone.utc).date()

        # Performance: activity per day, last 14 days (normalized 0-1).
        activity = get_recent_activity(user_id, days=14)
        daily = [0] * 14
        for row in activity:
            offset = (today - datetime.fromisoformat(row["created_at"]).date()).days
            if 0 <= offset < 14:
                daily[13 - offset] += 1
        peak = max(daily) or 1
        pulse = [round(v / peak, 3) for v in daily] if any(daily) else []

        # Blocker: the detector's own signal — recurring topics across the
        # founder's messages — counted per day over the last 7 days.
        user_msgs = [c for c in get_recent_conversations(user_id, days=7) if c["role"] == "user"]
        topic_days = {}
        for msg in user_msgs:
            offset = (today - datetime.fromisoformat(msg["created_at"]).date()).days
            if not 0 <= offset < 7:
                continue
            for kw in _keywords(msg["content"]):
                topic_days.setdefault(kw, [0] * 7)[6 - offset] += 1
        top = sorted(topic_days.items(), key=lambda kv: -sum(kv[1]))[:4]
        topics = [{"name": name.capitalize(), "days": [min(4, d) for d in days]} for name, days in top if sum(days) >= 2]

        # Enhancement: this stage's standard items, and whether each has come
        # up in conversation yet (the suggester's own test).
        convo_text = " ".join(c["content"].lower() for c in get_recent_conversations(user_id, days=90))
        active = next((p for p in get_phases(user_id) if p["status"] == "active"), None)
        stage = active["phase_name"] if active else user.get("stage")
        items = [
            {"name": e["enhancement"][:28], "value": 100 if e["enhancement"].lower() in convo_text else 15}
            for e in get_enhancements(user.get("business_type"), stage)[:6]
        ]

        return {
            "activity": pulse,
            "phases": _timeline_phases(get_phases(user_id)),
            "blocker_topics": topics,
            "coverage": items if len(items) >= 3 else [],
        }, 200

    # --- Onboarding: clarifying questions + pathway reveal ----------------

    @app.route("/api/v1/marquis/onboarding/questions", methods=["POST"])
    @require_auth
    def onboarding_questions():
        profile, problem = _onboarding_profile(request.get_json(silent=True) or {})
        if problem:
            return err(problem, 400)
        user_id = g.current_user["id"]
        try:
            update_user(user_id, {k: profile[k] for k in ("business_type", "stage", "description")})
        except Exception as e:
            print(f"[onboarding/questions] profile save failed (continuing): {e}")
        try:
            questions = generate_clarifying_questions(profile["business_type"], profile["stage"], profile["description"])
        except Exception as e:
            print(f"[onboarding/questions] generation failed: {e}")
            return err("The butler is unavailable right now.", 502)
        return {"questions": questions}, 200

    @app.route("/api/v1/marquis/onboarding/pathway", methods=["POST"])
    @require_auth
    def onboarding_pathway():
        data = request.get_json(silent=True) or {}
        profile, problem = _onboarding_profile(data)
        if problem:
            return err(problem, 400)
        answers = data.get("answers", [])
        if not isinstance(answers, list) or len(answers) > 5:
            return err("answers must be a list of at most 5 items.", 400)
        user_id = g.current_user["id"]
        try:
            pathway = generate_pathway(profile["business_type"], profile["stage"], profile["description"], answers)
        except Exception as e:
            print(f"[onboarding/pathway] generation failed: {e}")
            return err("The butler is unavailable right now.", 502)
        try:
            update_fields = {**{k: profile[k] for k in ("business_type", "stage", "description")}, "onboarding_complete": True}
            butler_name = _clean_butler_name(data.get("butler_name"))
            if butler_name:
                update_fields["butler_name"] = butler_name
            update_user(user_id, update_fields)
            if not get_phases(user_id):
                create_phase(user_id, 1, pathway["phase"]["name"], "active", pathway["phase"]["estimated_days"])
            create_activity_log(user_id, "onboarding_complete", {})
        except Exception as e:
            print(f"[onboarding/pathway] persisting failed (pathway still returned): {e}")
        return pathway, 200

    # --- Phase completion (Progress page, Animation 4 + 8) ------------------

    @app.route("/api/v1/marquis/phases/<phase_id>/complete", methods=["POST"])
    @require_auth
    def complete_phase(phase_id):
        user_id = g.current_user["id"]
        phase = get_phase_by_id(user_id, phase_id)
        if not phase:
            return err("Resource not found.", 404)
        if phase["status"] != "active":
            return err("Only the current phase can be marked complete.", 400)
        actual = max(1, round(days_since(phase.get("started_at")) or 0))
        updated = update_phase(user_id, phase_id, {"status": "complete", "actual_days": actual, "completed_at": _now_iso()})
        next_phase = next((p for p in get_phases(user_id) if p["phase_number"] == phase["phase_number"] + 1), None)
        if next_phase and next_phase["status"] == "future":
            update_phase(user_id, next_phase["id"], {"status": "active", "started_at": _now_iso()})
        create_activity_log(user_id, "phase_complete", {"phase_id": phase_id})
        notify_phase_complete(user_id, phase_id, phase["phase_name"], phase.get("estimated_days"), actual)
        return {"phase": {**phase, **(updated or {}), "status": "complete", "actual_days": actual}, "next_phase_id": next_phase["id"] if next_phase else None}, 200

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
        prior_activity = [a for a in get_recent_activity(user_id, days=14) if a not in week_activity]
        sessions_last_week = len({row["created_at"][:10] for row in prior_activity})

        # Combined gap vs estimate: finished phases' overrun, plus the active
        # phase's overrun so far (only once it's actually past its estimate).
        combined_gap = 0
        for p in all_phases:
            est = p.get("estimated_days")
            if not est:
                continue
            if p["status"] == "complete" and p.get("actual_days") is not None:
                combined_gap += p["actual_days"] - est
            elif p["status"] == "active":
                combined_gap += max(0, round(days_since(p.get("started_at")) or 0) - est)

        days_active = round(days_since(user.get("created_at")) or 0)

        # Exactly three cards, in the order the dashboard shows them. `trend`
        # drives the single directional arrow (up = gold, down = crimson).
        stat_cards = [
            {"label": "Days active", "value": str(days_active), "delta": "since you began", "trend": "flat", "warn": False},
            {
                "label": "Behind combined estimate" if combined_gap > 0 else "Against combined estimate",
                "value": f"+{combined_gap}d" if combined_gap > 0 else f"{combined_gap}d",
                "delta": "across every phase so far",
                "trend": "down" if combined_gap > 0 else "up" if combined_gap < 0 else "flat",
                "warn": combined_gap > 0,
            },
            {
                "label": "Sessions this week",
                "value": str(sessions_this_week),
                "delta": f"{sessions_last_week} the week before",
                "trend": "up" if sessions_this_week > sessions_last_week else "down" if sessions_this_week < sessions_last_week else "flat",
                "warn": False,
            },
        ]

        performance = run_performance_analyst(user_id)
        pathway = run_pathway_optimizer(user_id)
        blocker = run_blocker_detector(user_id)
        enhancement = run_enhancement_suggester(user_id)
        if enhancement.get("surface_now"):
            enhancement_text = f"{enhancement['surface_now']} — {enhancement.get('reason') or ''}".rstrip(" —")
        else:
            improvements = get_approved_prompt_improvements(limit=1)
            enhancement_text = (
                f"{improvements[0]['suggestion']} — {improvements[0].get('reason', '')}"
                if improvements
                else "Nothing new to surface this session."
            )

        agent_insights = [
            {"agent": "performance", "insight": performance.get("key_insight") or "No notable pattern yet.", "type": "heartbeat"},
            {
                "agent": "pathway",
                "insight": pathway.get("recommendation") or f"Pathway is {pathway.get('pathway_status') or 'on track'}.",
                "type": "delta",
            },
            {
                "agent": "blocker",
                "insight": blocker.get("likely_cause") or "No blocker signals detected.",
                "type": "warning",
            },
            {"agent": "enhancement", "insight": enhancement_text, "type": "suggestion"},
        ]

        # `action` values are real frontend page ids (conversation/progress/analytics).
        quick_links = [
            {"title": "Speak with Marquis", "sub": "Resume where you left off", "action": "conversation"},
            {"title": "The full pathway", "sub": "Every phase, against its estimate", "action": "progress"},
            {"title": "The four at work", "sub": "What each agent is watching", "action": "analytics"},
        ]

        if active_phase:
            elapsed = round(days_since(active_phase.get("started_at")) or 0)
            est = active_phase.get("estimated_days")
            sub = f"{active_phase['phase_name']}, day {elapsed}" + (f" of {est} estimated." if est else ".")
        else:
            sub = "No phase is underway yet."

        return {
            "greeting": _greeting(user.get("name")),
            "name": user.get("name"),
            "sub": sub,
            "active_phase_name": active_phase["phase_name"] if active_phase else None,
            "last_butler_exchange": last_butler_exchange,
            "last_butler_exchange_at": last_butler_msg["created_at"] if last_butler_msg else None,
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


def _agent_fields(entry_: dict, data: dict, require_settings: bool, current: Optional[dict] = None):
    """Validated user_agents fields from a request -> (fields, error or None)."""
    fields = {}
    if require_settings or "settings" in data:
        settings, problem = validate_settings(entry_, data.get("settings"))
        if problem:
            return None, problem
        fields["settings"] = settings
    run_mode = data.get("run_mode") or (current or {}).get("run_mode") or entry_["run_mode"]["default"]
    if run_mode not in ("on_demand", "scheduled"):
        return None, "run_mode must be on_demand or scheduled."
    if run_mode == "scheduled" and entry_["archetype"] != "watcher" and entry_["archetype"] != "reviewer":
        return None, "Only watchers and reviewers run on a schedule."
    hours = data.get("interval_hours", (current or {}).get("interval_hours") or entry_["run_mode"]["interval_hours"] or 24)
    if not isinstance(hours, int) or isinstance(hours, bool) or not LIMITS["min_interval_hours"] <= hours <= LIMITS["max_interval_hours"]:
        return None, f"interval_hours must be {LIMITS['min_interval_hours']}-{LIMITS['max_interval_hours']}."
    fields.update(run_mode=run_mode, interval_hours=hours)
    if run_mode == "scheduled" and (current is None or current.get("run_mode") != "scheduled"):
        fields["next_run_at"] = datetime.now(timezone.utc).isoformat()   # first run on the next cron tick
    return fields, None


# Messages of history sent to the model, read from the database.
MODEL_HISTORY_ROWS = 20


def _int_arg(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


def _time_arg(name: str):
    """(ISO timestamp or None, bad) for an optional timestamp query argument."""
    raw = request.args.get(name)
    if not raw:
        return None, False
    raw = raw.replace(" ", "+")                    # a '+' in a URL arrives as a space
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return None, True
    return raw, False


def _exchange_arg(data: dict) -> Optional[str]:
    value = data.get("exchange_id")
    return value if canvas_items_mod.is_uuid(value) else None


def _save_item(user_id: str, kind: str, title, spec: dict, exchange_id: Optional[str], live_spec: Optional[dict] = None):
    """Validate and store one creation; returns the item as the page sees it,
    or None. Saving is fail-soft: a failure never costs the user their answer."""
    clean = canvas_items_mod.clean_spec(kind, spec)
    if not clean:
        print(f"[canvas] not saved, {kind} spec failed validation")
        return None
    try:
        row = create_canvas_item(user_id, kind, str(title or clean.get("title") or "")[:80], clean, exchange_id)
    except Exception as e:
        print(f"[canvas] saving {kind} failed: {e}")
        return None
    item = canvas_items_mod.public(row)
    if live_spec is not None:
        item["spec"] = live_spec                    # e.g. a timeline with its phases filled in
    return item


def _save_exchange_items(user_id: str, exchange_id: str, visualization, sources: list) -> list:
    """The creation an exchange produced (a canvas block, or the sources read)."""
    if visualization:
        spec = {**visualization, **({"sources": sources} if sources else {})}
        item = _save_item(user_id, canvas_items_mod.kind_for(visualization["type"]), visualization.get("title"),
                          spec, exchange_id, live_spec=spec)
    elif sources:
        item = _save_item(user_id, "sources", "What I read on your behalf",
                          {"type": "sources", "title": "What I read on your behalf", "sources": sources}, exchange_id)
    else:
        return []
    return [item] if item else []


def _live_item(user_id: str, row: dict) -> dict:
    """An item with data that must be current filled in when it is opened."""
    item = canvas_items_mod.public(row)
    if row["kind"] == "phase_timeline":
        item["spec"] = {**(row.get("spec") or {}), "phases": _timeline_phases(get_phases(user_id))}
    return item


def _public_sources(sources: list) -> list:
    return [{"n": i, "title": s["title"], "url": s["url"]} for i, s in enumerate(sources, 1)]


def _answer_with_web(message: str, query: str, reply_style: str = "brief"):
    """Read the web for `query` and answer `message` from it -> (reply, visualization, sources).

    Never raises: a failed search or answer becomes an honest sentence.
    """
    try:
        sources = research(query)
    except Exception as e:
        print(f"[conversation] web search failed: {e}")
        return "I tried to look that up, but the web isn't answering just now.", None, []
    if not sources:
        return "I looked, and found nothing on the web that answers that.", None, []
    try:
        reply, visualization = answer_from_web(message, sources, reply_style)
    except Exception as e:
        print(f"[conversation] web answer failed: {e}")
        return "I read the pages, but couldn't compose an answer just now.", None, []
    return reply, visualization, _public_sources(sources)


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timeline_phases(rows: list) -> list:
    """Phases in the shape the canvas's phase_timeline view renders."""
    out = []
    for p in rows:
        status = {"complete": "done", "active": "current"}.get(p["status"], "future")
        actual = p.get("actual_days")
        if status == "current":
            actual = round(days_since(p.get("started_at")) or 0)
        out.append({"name": p["phase_name"], "est_days": p.get("estimated_days"), "actual_days": actual, "status": status})
    return out


# Curated butler names (MARQUIS_product.md "Butler Screen Redesign") —
# formal, British, Alfred-adjacent. Validated server-side rather than
# trusted from the client, same posture as every other onboarding field.
BUTLER_NAMES = ("Reeves", "Sterling", "Ashford", "Camden", "Dorian", "Whitmore", "Hale", "Aldric")


def _clean_butler_name(value) -> Optional[str]:
    """A valid curated name (canonical casing), or None to leave it unset/unchanged."""
    if not isinstance(value, str):
        return None
    match = value.strip().casefold()
    return next((n for n in BUTLER_NAMES if n.casefold() == match), None)


_ONBOARDING_LIMITS = {"business_type": 80, "stage": 60, "description": 6000}


def _onboarding_profile(data: dict):
    """Validate the onboarding profile fields. Returns (profile, error_message)."""
    profile = {}
    for key, limit in _ONBOARDING_LIMITS.items():
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None, f"{key} is required."
        if len(value) > limit:
            return None, f"{key} exceeds {limit} characters."
        profile[key] = value.strip()
    return profile, None


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
