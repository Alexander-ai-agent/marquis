"""Chat history: group stored conversation rows into exchanges.

An exchange is a question and Alfred's answer. New rows share an
exchange_id; older rows (from before it existed) are paired by order:
each user row with the butler row that follows it.
"""
from typing import Optional

MAX_PAGE = 50
DEFAULT_PAGE = 20


def _exchange(user_row: Optional[dict], butler_row: Optional[dict]) -> dict:
    anchor = user_row or butler_row
    return {
        "id": anchor.get("exchange_id") or f"row-{anchor['id']}",
        "you": {"content": user_row["content"], "created_at": user_row["created_at"]} if user_row else None,
        "alfred": {"content": butler_row["content"], "created_at": butler_row["created_at"]} if butler_row else None,
        "sources": ((butler_row or {}).get("meta") or {}).get("sources") or [],
    }


def group_exchanges(rows_asc: list) -> list:
    """Oldest-first rows -> oldest-first exchanges. An unanswered question is
    an exchange with alfred = None. A legacy butler row with no question
    before it (its question lies on an older page) is skipped."""
    out, by_id, pending = [], {}, None

    def flush():
        nonlocal pending
        if pending:
            out.append(pending)
            pending = None

    for row in rows_asc:
        eid = row.get("exchange_id")
        if eid:
            flush()
            slot = by_id.get(eid)
            if slot is None:
                slot = by_id[eid] = {"user": None, "butler": None}
                out.append(slot)
            slot["user" if row["role"] == "user" else "butler"] = row
        elif row["role"] == "user":
            flush()
            pending = {"user": row, "butler": None}
        elif pending:                                    # the butler row answering the pending question
            pending["butler"] = row
            flush()
    flush()
    return [_exchange(e["user"], e["butler"]) for e in out]


def page(rows_desc: list, limit: int) -> dict:
    """One page of history from newest-first rows fetched with limit*2+1 rows.

    Returns {exchanges (newest first), has_more, next_before}. An oldest
    exchange cut off by the fetch boundary (its question is on an older page)
    is dropped here and delivered whole on the next page.
    """
    full = len(rows_desc) >= limit * 2 + 1
    exchanges = group_exchanges(list(reversed(rows_desc)))
    if full and exchanges and exchanges[0]["you"] is None:
        exchanges = exchanges[1:]
    has_more = full or len(exchanges) > limit
    exchanges = exchanges[-limit:]
    oldest = exchanges[0]["you"]["created_at"] if exchanges and exchanges[0]["you"] else None
    return {"exchanges": list(reversed(exchanges)), "has_more": bool(has_more and oldest), "next_before": oldest}


def model_messages(rows_asc: list) -> list:
    """Rows -> the model's message list (oldest first, starting with a user turn)."""
    messages = [{"role": "assistant" if r["role"] == "butler" else "user", "content": r["content"]}
                for r in rows_asc if r.get("role") in ("user", "butler") and r.get("content")]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages
