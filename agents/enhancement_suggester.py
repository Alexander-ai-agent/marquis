"""Agent 4 — Enhancement Suggester.

Matches the user's business profile and progress against a hardcoded
enhancement library. Surfaces one option at a time, never mandates.
"""
from enhancement_library import ENHANCEMENT_LIBRARY, get_enhancements, normalize_business_type
from supabase_client import get_active_phase, get_recent_conversations, get_user_by_id


def _already_mentioned(enhancement: str, conversations: list) -> bool:
    """Naive check: has this enhancement's name already come up in conversation?"""
    needle = enhancement.lower()
    return any(needle in c["content"].lower() for c in conversations)


def run_enhancement_suggester(user_id: str, supabase=None) -> dict:
    """Find the single most relevant unmentioned enhancement for this user's stage."""
    user = get_user_by_id(user_id)
    if not user:
        return {"surface_now": None, "reason": None}

    business_type = user.get("business_type")
    # Prefer the active phase when it's a library stage (Foundation, Build,
    # Launch, Revenue, Scale); otherwise fall back to the onboarding stage.
    stage = user.get("stage")
    try:
        active = get_active_phase(user_id)
    except Exception:
        active = None
    library_stages = ENHANCEMENT_LIBRARY.get(normalize_business_type(business_type), {})
    if active and active.get("phase_name") in library_stages:
        stage = active["phase_name"]
    candidates = get_enhancements(business_type, stage)
    if not candidates:
        return {"surface_now": None, "reason": None}

    conversations = get_recent_conversations(user_id, days=90)

    priority_order = {"urgent": 0, "standard": 1, "nice_to_have": 2}
    for candidate in sorted(candidates, key=lambda c: priority_order.get(c["priority"], 3)):
        if not _already_mentioned(candidate["enhancement"], conversations):
            return {
                "surface_now": candidate["enhancement"],
                "reason": candidate["reason"],
            }

    return {"surface_now": None, "reason": None}
