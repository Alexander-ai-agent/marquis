"""Each enabled specialist with its latest run: what the Agents screen shows
and (capped, sanitized) what Alfred reads in AGENT CONTEXT."""
from specialists import store
from specialists.catalog import entry as catalog_entry, public
from specialists.safety import context_block


def my_agents(user_id: str) -> list:
    """Enabled specialists, each with its catalog entry and latest non-error run."""
    rows = store.list_user_agents(user_id)
    runs = store.latest_runs(user_id, limit=100)
    latest = {}
    for r in runs:  # newest first
        if r["user_agent_id"] not in latest and r["status"] in ("ok", "skipped"):
            latest[r["user_agent_id"]] = r
    out = []
    for ua in rows:
        e = catalog_entry(ua["agent_key"])
        if not e:
            continue
        run = latest.get(ua["id"])
        out.append({
            "id": ua["id"], "agent": public(e), "settings": ua.get("settings") or {}, "state": ua["state"],
            "enabled": ua["enabled"], "run_mode": ua["run_mode"], "interval_hours": ua.get("interval_hours"),
            "last_run_at": ua.get("last_run_at"), "next_run_at": ua.get("next_run_at"),
            "latest": {"output": run["output"], "created_at": run["created_at"], "flagged": run["flagged"]} if run else None,
        })
    return out


def alfred_context(user_id: str) -> str:
    """Sanitized, capped lines for Alfred; FLAGGED agents first."""
    agents = [a for a in my_agents(user_id) if a["enabled"]]
    agents.sort(key=lambda a: a["state"] != "FLAGGED")
    findings = []
    for a in agents:
        output = (a["latest"] or {}).get("output") or {}
        findings.append({"name": a["agent"]["name"], "state": a["state"],
                         "summary": output.get("summary") or "No run yet.",
                         "items": output.get("items") or [], "data_note": output.get("data_note")})
    return context_block(findings)
