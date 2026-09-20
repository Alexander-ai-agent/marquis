"""Agent 3 — Blocker Detector.

Monitors for stuck signals before the user realises they're stuck. Never
tells the user directly — gives the butler context to ask the right
question at the right time.
"""
import re
from collections import Counter
from datetime import datetime, timezone

from supabase_client import get_recent_activity, get_recent_conversations

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for",
    "is", "it", "this", "that", "i", "you", "we", "my", "your", "our",
    "with", "how", "what", "do", "does", "should", "can", "about",
}


def _keywords(text: str) -> set:
    """Lowercase, stopword-stripped words from a message, for topic overlap checks."""
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _repeated_topic(user_messages: list) -> tuple:
    """Find a keyword mentioned across 3+ separate user messages, if any."""
    counts = Counter()
    for msg in user_messages:
        for kw in _keywords(msg["content"]):
            counts[kw] += 1
    for word, count in counts.most_common(5):
        if count >= 3:
            return word, count
    return None, 0


def run_blocker_detector(user_id: str, supabase=None) -> dict:
    """Detect hard/soft/confused/decision/momentum stuck states."""
    conversations = get_recent_conversations(user_id, days=14)
    activity_logs = get_recent_activity(user_id, days=14)
    user_messages = [c for c in conversations if c["role"] == "user"]

    last_activity_at = None
    if activity_logs:
        last_activity_at = max(row["created_at"] for row in activity_logs)
    elif conversations:
        last_activity_at = max(c["created_at"] for c in conversations)

    days_inactive = 0
    if last_activity_at:
        last_dt = datetime.fromisoformat(last_activity_at)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        days_inactive = (datetime.now(timezone.utc) - last_dt).days
    else:
        days_inactive = 14

    repeated_word, repeat_count = _repeated_topic(user_messages)

    if days_inactive >= 5:
        return {
            "stuck_type": "hard",
            "confidence": min(0.6 + days_inactive * 0.05, 0.95),
            "likely_cause": (
                f"No activity for {days_inactive} days."
                + (f" Last recurring topic: '{repeated_word}'." if repeated_word else "")
            ),
            "urgency": "immediate",
        }

    if repeat_count >= 3:
        return {
            "stuck_type": "decision",
            "confidence": min(0.4 + repeat_count * 0.1, 0.9),
            "likely_cause": f"The topic '{repeated_word}' has come up in {repeat_count} separate messages without resolution.",
            "urgency": "surface",
        }

    if days_inactive >= 3:
        return {
            "stuck_type": "momentum",
            "confidence": 0.5,
            "likely_cause": f"No activity for {days_inactive} days — an early slowdown signal.",
            "urgency": "watch",
        }

    if len(user_messages) >= 4 and not activity_logs:
        return {
            "stuck_type": "confused",
            "confidence": 0.45,
            "likely_cause": "Frequent conversation activity with no corresponding activity_logs events — questions may not be converting to action.",
            "urgency": "watch",
        }

    return {
        "stuck_type": "none",
        "confidence": 0.9,
        "likely_cause": None,
        "urgency": "none",
    }
