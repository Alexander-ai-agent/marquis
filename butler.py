"""Butler system prompt (hardcoded, per spec) and Claude conversation calls.

Living Canvas (MARQUIS_product.md, Sep 24): a butler reply may carry an
optional structured visualization payload alongside its prose. The model
emits it only when concretely explaining something visualizable, as one
trailing sentinel line:

    <<CANVAS {"type": "...", ...}>>

parse_visualization() strips that line from the prose ALWAYS (the user
never sees the raw tag) and returns the payload only if it parses and
passes the allow-list + shape checks below. Anything else -> None, and the
frontend simply stays in its idle state. Fail-soft by construction.
"""
import json
import re
from typing import Optional, Tuple

import anthropic

from config import Config
from enhancement_library import normalize_business_type

_client: Optional[anthropic.Anthropic] = None

MAX_HISTORY_MESSAGES = 20

BUTLER_SYSTEM_PROMPT_TEMPLATE = (
    "You are the Marquis butler. A guide with the mindset of someone who has already "
    "built something significant. You never say that. The quality of your thinking reveals it.\n\n"
    "You give ideas, suggestions and recommendations freely. The one thing you never do: "
    "question the fundamental business vision or suggest pivoting the core idea.\n\n"
    "You never say: Great question, I'd be happy to help, Let's get started, That sounds exciting.\n\n"
    "You say: Three things require your attention. This approach will cost you six weeks. "
    "You've asked this before. The answer hasn't changed.\n\n"
    "No spoon feeding. Show the path. They walk it. Warm but never performatively warm. "
    "Speak in complete paragraphs. Never bullet points. Never more than 3 paragraphs per response.\n\n"
    "AGENT CONTEXT: {agent_context}"
)

# Appended after the hardcoded prompt above (which stays byte-for-byte as
# specified). Tells the model when and how to push a visual to the canvas.
CANVAS_INSTRUCTIONS = (
    "\n\nLIVING CANVAS: The user sees a canvas beside your words. Only when you are concretely "
    "explaining something that is genuinely clearer as a picture, end your reply with ONE final line, "
    "exactly: <<CANVAS {json}>> — and nothing after it. On ordinary conversational turns, omit it entirely. "
    "Allowed types and shapes (use only figures the user has given you or that appear in AGENT CONTEXT; "
    "never invent numbers):\n"
    '- {"type":"revenue_projection","title":str,"unit":"$","points":[{"label":str,"value":number,"projected":bool}]}  (2-24 points)\n'
    '- {"type":"phase_timeline","title":str}  (the real phase data is filled in for you)\n'
    '- {"type":"blocker_heat","title":str,"topics":[{"name":str,"days":[7 integers 0-4]}]}  (1-6 topics)\n'
    '- {"type":"activity_pulse","title":str,"values":[numbers 0-1]}  (7-30 values)\n'
    '- {"type":"coverage","title":str,"items":[{"name":str,"value":0-100}]}  (3-8 items)'
)

CANVAS_TYPES = ("revenue_projection", "phase_timeline", "blocker_heat", "activity_pulse", "coverage")
_CANVAS_LINE = re.compile(r"\n?[ \t]*<<CANVAS\b(.*?)>>[ \t]*$", re.DOTALL)
_ANY_CANVAS_TAG = re.compile(r"<<CANVAS\b.*?(>>|$)", re.DOTALL)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _clean_title(payload: dict) -> str:
    title = payload.get("title")
    return title.strip()[:80] if isinstance(title, str) else ""


def validate_visualization(payload) -> Optional[dict]:
    """Return a sanitized copy of an allowed payload, or None."""
    if not isinstance(payload, dict) or payload.get("type") not in CANVAS_TYPES:
        return None
    kind = payload["type"]
    out = {"type": kind, "title": _clean_title(payload)}

    if kind == "revenue_projection":
        pts = payload.get("points")
        if not isinstance(pts, list) or not 2 <= len(pts) <= 24:
            return None
        clean = []
        for p in pts:
            if not isinstance(p, dict) or not isinstance(p.get("label"), str) or not _num(p.get("value")):
                return None
            clean.append({"label": p["label"][:24], "value": float(p["value"]), "projected": bool(p.get("projected"))})
        out["points"] = clean
        out["unit"] = payload.get("unit") if payload.get("unit") in ("$", "₹", "€", "£", "") else "$"
        return out

    if kind == "phase_timeline":
        return out  # data is filled server-side from the phases table

    if kind == "blocker_heat":
        topics = payload.get("topics")
        if not isinstance(topics, list) or not 1 <= len(topics) <= 6:
            return None
        clean = []
        for t in topics:
            days = t.get("days") if isinstance(t, dict) else None
            if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not isinstance(days, list) or len(days) != 7:
                return None
            if not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 4 for d in days):
                return None
            clean.append({"name": t["name"][:28], "days": days})
        out["topics"] = clean
        return out

    if kind == "activity_pulse":
        vals = payload.get("values")
        if not isinstance(vals, list) or not 7 <= len(vals) <= 30 or not all(_num(v) and 0 <= v <= 1 for v in vals):
            return None
        out["values"] = [float(v) for v in vals]
        return out

    if kind == "coverage":
        items = payload.get("items")
        if not isinstance(items, list) or not 3 <= len(items) <= 8:
            return None
        clean = []
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("name"), str) or not _num(it.get("value")):
                return None
            clean.append({"name": it["name"][:28], "value": max(0.0, min(100.0, float(it["value"])))})
        out["items"] = clean
        return out

    return None


def parse_visualization(reply: str) -> Tuple[str, Optional[dict]]:
    """Split a raw model reply into (prose, visualization-or-None).

    The sentinel is removed from the prose in every case — valid, invalid,
    malformed, or mid-text — so a parse failure can never leak into what
    the user reads or hears.
    """
    if not isinstance(reply, str):
        return "", None
    viz = None
    match = _CANVAS_LINE.search(reply.rstrip())
    if match:
        try:
            viz = validate_visualization(json.loads(match.group(1).strip()))
        except (ValueError, TypeError):
            viz = None
    prose = _ANY_CANVAS_TAG.sub("", reply).strip()
    return prose, viz


def get_client() -> anthropic.Anthropic:
    """Return a lazily-initialized singleton Anthropic client."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    return _client


def build_agent_context(performance: dict, pathway: dict, blocker: dict, enhancement: dict) -> dict:
    """Collapse each agent's output into one string, per the /conversation response shape."""
    performance_text = performance.get("key_insight") or "No notable pattern yet."

    pathway_text = pathway.get("recommendation") or f"Pathway is {pathway.get('pathway_status', 'on_track')}."

    if blocker.get("stuck_type") not in (None, "none"):
        blocker_text = blocker.get("likely_cause") or f"Possible {blocker['stuck_type']} stuck signal."
    else:
        blocker_text = "No blocker signals detected."

    if enhancement.get("surface_now"):
        enhancement_text = f"{enhancement['surface_now']} — {enhancement.get('reason', '')}"
    else:
        enhancement_text = "Nothing new to surface this session."

    return {
        "performance": performance_text,
        "pathway": pathway_text,
        "blocker": blocker_text,
        "enhancement": enhancement_text,
    }


def _format_agent_context_block(agent_context: dict, prompt_improvements: list) -> str:
    """Render the agent_context dict (+ any approved prompt_improvements) as one string.

    This is exactly what fills {agent_context} in the hardcoded system
    prompt — kept as a single substitution so the rest of the prompt text
    stays byte-for-byte what was specified ("hardcoded — paste exactly").
    """
    lines = [
        f"- {label.capitalize()}: {agent_context[label]}"
        for label in ("performance", "pathway", "blocker", "enhancement")
    ]
    if prompt_improvements:
        lines.append("Approved behavior improvements to apply:")
        lines.extend(f"- {row['suggestion']} ({row.get('reason', '')})" for row in prompt_improvements)
    return "\n".join(lines)


# Adaptive Interface (MARQUIS_product.md, Sep 24): same butler, same design
# system, different mission per business type. This is vocabulary only —
# it changes the words the butler reaches for, never what it's allowed to
# say (the hardcoded rules above still apply to every business type).
# Keyed by enhancement_library.normalize_business_type()'s canonical names
# so one normalization function serves both the enhancement library and
# this vocabulary lookup. "Agency" and "Service" from the task brief are
# merged into "Service/Agency" — the onboarding taxonomy only has one such
# type (MARQUIS_product.md's "Supported Business Types" lists "Service
# business / Agency" as a single entry, not two).
BUSINESS_VOCABULARY = {
    "SaaS": "MRR, CAC, LTV, churn, activation, product-led growth, ARR, NPS",
    "E-commerce": "GMV, AOV, ROAS, inventory, conversion rate, repeat purchase rate",
    "Trading": "P&L, drawdown, win rate, RR, position sizing, portfolio allocation, realized/unrealized gains",
    "Service/Agency": "utilisation, retainers, margins, delivery, billable hours, client pipeline, close rate, capacity, referral rate",
    "Marketplace": "GMV, take rate, liquidity, supply/demand balance, NPS both sides",
    "Content/Creator": "CPM, sponsorships, audience growth, retention, engagement rate",
    "Mobile App": "DAU, MAU, D1/D7/D30 retention, ARPU, ASO, session length",
}


def _vocabulary_instructions(business_type: Optional[str]) -> str:
    """The optional vocabulary appendix for one business type, or "" if none matches."""
    vocab = BUSINESS_VOCABULARY.get(normalize_business_type(business_type)) if business_type else None
    if not vocab:
        return ""
    return (
        "\n\nVOCABULARY: This founder's business is best described in terms like: "
        f"{vocab}. Reach for these naturally when they fit what you're actually saying — "
        "never force a term in just to sound fluent, and never explain jargon you didn't need to use."
    )


def build_system_prompt(agent_context: dict, prompt_improvements: list, business_type: Optional[str] = None) -> str:
    """Fill the hardcoded butler system prompt's one placeholder: {agent_context}."""
    return (
        BUTLER_SYSTEM_PROMPT_TEMPLATE.format(
            agent_context=_format_agent_context_block(agent_context, prompt_improvements)
        )
        + CANVAS_INSTRUCTIONS
        + _vocabulary_instructions(business_type)
    )


def _build_messages(history: Optional[list], message: str) -> list:
    """Build the Claude messages array from prior turns plus the new message."""
    messages = []
    if isinstance(history, list):
        for item in history[-MAX_HISTORY_MESSAGES:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            # Claude's API only accepts 'user'/'assistant' — the app's own
            # 'butler' role (used in Supabase) is mapped here.
            claude_role = "assistant" if role == "butler" else role
            if claude_role in ("user", "assistant") and isinstance(content, str) and content:
                messages.append({"role": claude_role, "content": content})
    messages.append({"role": "user", "content": message})
    return messages


def get_butler_response(message: str, system_prompt: str, history: Optional[list] = None) -> str:
    """Call Claude with the butler system prompt and return the full reply text (no streaming)."""
    response = get_client().messages.create(
        model=Config.CLAUDE_MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=_build_messages(history, message),
    )
    return response.content[0].text.strip()


# --- Onboarding (MARQUIS_product.md, "Onboarding flow" steps 4 and 5) --------

ONBOARDING_SYSTEM_PROMPT = (
    "You are the Marquis butler meeting a founder for the first time. Formal, composed, honest. "
    "You never say Great idea, Great question, I'd be happy to help, Let's get started, or That sounds exciting. "
    "You never validate without evidence. You never give generic advice. You never question the core "
    "business vision or suggest a pivot. You never ask a question the founder has already answered in "
    "what they shared. Respond with JSON only — no prose before or after it."
)


def _extract_json(text: str):
    """Pull the first JSON object/array out of a model reply."""
    if not isinstance(text, str):
        raise ValueError("no text")
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise ValueError("no JSON found")
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("unterminated JSON")
    return json.loads(text[start : end + 1])


def _profile_block(business_type: str, stage: str, description: str) -> str:
    return f"Business type: {business_type}\nStage: {stage}\nIn their words:\n{description}"


def _ask_json(user_content: str, max_tokens: int = 900):
    response = get_client().messages.create(
        model=Config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=ONBOARDING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return _extract_json(response.content[0].text)


def generate_clarifying_questions(business_type: str, stage: str, description: str) -> list:
    """3-5 questions specific to what the founder described. Raises on bad output."""
    data = _ask_json(
        _profile_block(business_type, stage, description)
        + "\n\nAsk 3 to 5 clarifying questions specific to what they described — the questions that "
        "most change what phase 1 should be. Skip anything they already told you. "
        'Return exactly: {"questions": ["...", "..."]}'
    )
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        raise ValueError("questions missing")
    clean = [q.strip()[:220] for q in questions if isinstance(q, str) and q.strip()]
    if not 3 <= len(clean) <= 5:
        raise ValueError("expected 3-5 questions")
    return clean


def generate_pathway(business_type: str, stage: str, description: str, answers: list) -> dict:
    """The pathway reveal: honest assessment, phase 1, tools + cost, success, this week."""
    qa = "\n".join(
        f"Q: {a.get('question', '')}\nA: {a.get('answer', '') or '(no answer)'}"
        for a in answers if isinstance(a, dict)
    )
    data = _ask_json(
        _profile_block(business_type, stage, description)
        + f"\n\nTheir answers to your clarifying questions:\n{qa}\n\n"
        "Give the pathway reveal. Assessment, not validation. Phase 1 only — do not reveal later phases. "
        'Return exactly: {"assessment": str (2-3 sentences), "phase": {"name": str, '
        '"estimated_days": int, "actions": [2-4 str], "tools": [{"name": str, "cost": str}], '
        '"success": str}, "this_week": str (the one thing to do this week)}',
        max_tokens=1200,
    )
    phase = data.get("phase") if isinstance(data, dict) else None
    if not isinstance(phase, dict) or not isinstance(data.get("assessment"), str) or not isinstance(data.get("this_week"), str):
        raise ValueError("pathway shape invalid")
    est = phase.get("estimated_days")
    return {
        "assessment": data["assessment"].strip(),
        "this_week": data["this_week"].strip(),
        "phase": {
            "name": str(phase.get("name") or "Foundation").strip()[:40],
            "estimated_days": int(est) if isinstance(est, (int, float)) and 1 <= est <= 180 else 14,
            "actions": [str(a).strip() for a in (phase.get("actions") or []) if str(a).strip()][:4],
            "tools": [
                {"name": str(t.get("name", "")).strip()[:40], "cost": str(t.get("cost", "")).strip()[:40]}
                for t in (phase.get("tools") or []) if isinstance(t, dict) and t.get("name")
            ][:5],
            "success": str(phase.get("success") or "").strip(),
        },
    }
