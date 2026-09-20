"""Agent 2 — Pathway Optimizer.

Reviews whether the current phase sequence still makes sense given actual
progress. Never tells the user directly — informs the butler only.
"""
from datetime import datetime, timezone

from supabase_client import get_phases


def run_pathway_optimizer(user_id: str, supabase=None) -> dict:
    """Check current phase timing against estimate and flag pathway drift."""
    phases = get_phases(user_id)
    current = next((p for p in phases if p["status"] == "active"), None)

    if not current:
        return {
            "pathway_status": "on_track",
            "recommendation": None,
            "urgency": "next_week",
        }

    estimated = current.get("estimated_days") or 0
    if current.get("started_at"):
        started = datetime.fromisoformat(current["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        actual_days = (datetime.now(timezone.utc) - started).days
    else:
        actual_days = 0

    completed = [p for p in phases if p["status"] == "complete" and p.get("actual_days")]
    overruns = sum(
        1 for p in completed if p.get("estimated_days") and p["actual_days"] > p["estimated_days"] * 1.3
    )

    if estimated and actual_days > estimated * 2:
        return {
            "pathway_status": "significant_change",
            "recommendation": (
                f"'{current['phase_name']}' is more than double its {estimated}-day "
                "estimate. Reassess whether the phase scope has grown or a blocker "
                "is being avoided."
            ),
            "urgency": "immediate",
        }
    if estimated and actual_days > estimated * 1.5:
        return {
            "pathway_status": "needs_adjustment",
            "recommendation": (
                f"'{current['phase_name']}' is running well past its {estimated}-day "
                "estimate. Worth surfacing the gap in the next conversation."
            ),
            "urgency": "next_conversation",
        }
    if overruns >= 2:
        return {
            "pathway_status": "needs_adjustment",
            "recommendation": (
                "Multiple prior phases ran significantly over estimate — the "
                "pathway may be underestimating this user's pace consistently."
            ),
            "urgency": "next_week",
        }

    return {
        "pathway_status": "on_track",
        "recommendation": None,
        "urgency": "next_week",
    }
