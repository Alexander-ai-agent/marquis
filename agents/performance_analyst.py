"""Agent 1 — Performance Analyst.

Reads activity, phase progress, and time data to surface patterns to the
butler. Never speaks to the user directly.
"""
from collections import Counter
from datetime import datetime, timezone

from supabase_client import (
    get_active_phase,
    get_recent_activity,
    get_recent_conversations,
)


def _week_bucket(iso_timestamp: str) -> str:
    """ISO year-week string for grouping timestamps into weeks."""
    dt = datetime.fromisoformat(iso_timestamp)
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week}"


def _activity_pattern(activity_logs: list) -> str:
    """Classify weekly activity counts as consistent, bursty, declining, or inactive."""
    if not activity_logs:
        return "inactive"

    weeks = Counter(_week_bucket(row["created_at"]) for row in activity_logs)
    counts = list(weeks.values())
    if len(counts) < 2:
        return "consistent" if counts and counts[0] > 0 else "inactive"

    mean = sum(counts) / len(counts)
    variance = sum((c - mean) ** 2 for c in counts) / len(counts)
    stdev = variance ** 0.5

    sorted_weeks = sorted(weeks.items())
    first_half = sum(c for _, c in sorted_weeks[: len(sorted_weeks) // 2]) or 1
    second_half = sum(c for _, c in sorted_weeks[len(sorted_weeks) // 2 :])

    if second_half < first_half * 0.5:
        return "declining"
    if mean > 0 and stdev / mean > 0.6:
        return "bursty"
    return "consistent"


def run_performance_analyst(user_id: str, supabase=None) -> dict:
    """Analyse the last 30 days of activity, phases, and conversations for this user.

    `supabase` is accepted for interface parity with the spec but unused —
    queries go through the shared supabase_client module directly.
    """
    conversations = get_recent_conversations(user_id, days=30)
    activity_logs = get_recent_activity(user_id, days=30)
    current_phase = get_active_phase(user_id)

    if current_phase and current_phase.get("started_at"):
        started = datetime.fromisoformat(current_phase["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        current_phase_days = (datetime.now(timezone.utc) - started).days
    else:
        current_phase_days = 0
    estimated = (current_phase or {}).get("estimated_days") or 0
    gap = current_phase_days - estimated

    pattern = _activity_pattern(activity_logs)

    if not current_phase:
        key_insight = "No active phase found for this user."
    elif estimated and gap > estimated * 0.4:
        key_insight = (
            f"Current phase '{current_phase['phase_name']}' is {gap} days over "
            f"its {estimated}-day estimate."
        )
    elif pattern == "declining":
        key_insight = "Activity has dropped sharply in the second half of the last 30 days."
    elif pattern == "inactive":
        key_insight = "No recorded activity in the last 30 days."
    elif estimated and current_phase_days < estimated * 0.6:
        key_insight = (
            f"Current phase is tracking ahead of its {estimated}-day estimate."
        )
    else:
        key_insight = f"Progress on '{current_phase['phase_name']}' is on pace with estimate."

    return {
        "phase_timing": {
            "current_phase_days": current_phase_days,
            "estimated": estimated,
            "gap": gap,
        },
        "activity_pattern": pattern,
        "key_insight": key_insight,
        "_conversation_count": len(conversations),
    }
