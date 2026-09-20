"""Best-effort n8n webhook calls.

Both webhooks are optional integrations outside the 6 required REST
endpoints — if their URL env var is unset, or the call fails, the caller's
request must not fail because of it. Errors are logged and swallowed.
"""
from typing import Optional

import requests

from config import Config

_TIMEOUT_SECONDS = 5


def notify_phase_complete(
    user_id: str, phase_id: str, phase_name: str, estimated_days: Optional[int], actual_days: Optional[int]
) -> None:
    """POST to the n8n phase-completion webhook, if configured.

    Body matches n8n's marquis_phase_completion.json expectation exactly:
    {user_id, phase_id, phase_name, estimated_days, actual_days}. n8n owns
    marking the phase row 'complete' and emailing the user from there.
    """
    if not Config.N8N_PHASE_COMPLETE_URL:
        return
    try:
        requests.post(
            Config.N8N_PHASE_COMPLETE_URL,
            json={
                "user_id": user_id,
                "phase_id": phase_id,
                "phase_name": phase_name,
                "estimated_days": estimated_days,
                "actual_days": actual_days,
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        print(f"[webhook_client] phase-complete webhook failed: {e}")


def trigger_onboarding_webhook(user_id: str, email: str, name: str) -> None:
    """POST to an optional n8n onboarding-flow webhook after signup."""
    if not Config.N8N_ONBOARDING_URL:
        return
    try:
        requests.post(
            Config.N8N_ONBOARDING_URL,
            json={"user_id": user_id, "email": email, "name": name},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        print(f"[webhook_client] onboarding webhook failed: {e}")
