"""GET endpoints: dashboard, phases, user/profile, and CORS behavior."""
from conftest import FRONTEND_URL

DASHBOARD_URL = "/api/v1/marquis/dashboard"
PHASES_URL = "/api/v1/marquis/phases"
PROFILE_URL = "/api/v1/marquis/user/profile"


# --- test_user_profile -----------------------------------------------------

def test_user_profile_authenticated(client, auth_headers):
    user, headers = auth_headers

    resp = client.get(PROFILE_URL, headers=headers)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == user["id"]
    assert body["email"] == user["email"]
    assert body["onboarding_complete"] is False
    assert set(body.keys()) == {
        "id", "name", "email", "business_type", "stage",
        "description", "onboarding_complete", "created_at",
    }


def test_user_profile_unauthenticated(client):
    resp = client.get(PROFILE_URL)
    assert resp.status_code == 401


# --- test_dashboard ---------------------------------------------------

def test_dashboard_authenticated(client, auth_headers, fake_db):
    user, headers = auth_headers
    fake_db.add_phase(user["id"], phase_number=1, phase_name="Foundation", status="complete", actual_days=10, estimated_days=14)
    fake_db.add_phase(user["id"], phase_number=2, phase_name="Build", status="active", estimated_days=20)
    fake_db.create_conversation(user["id"], "user", "How do I price this?")
    fake_db.create_conversation(user["id"], "butler", "Three things require your attention.")

    resp = client.get(DASHBOARD_URL, headers=headers)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["greeting"].endswith(f"{user['name']}.") or body["greeting"].endswith(".")
    assert body["last_butler_exchange"] == "Three things require your attention."
    assert body["phase_ring"] == {"current_phase": 2, "total_phases": 2, "percent_complete": 50.0}
    assert len(body["stat_cards"]) == 4
    assert [i["agent"] for i in body["agent_insights"]] == ["performance", "pathway", "blocker", "enhancement"]
    assert [i["type"] for i in body["agent_insights"]] == ["heartbeat", "delta", "warning", "suggestion"]
    assert len(body["quick_links"]) == 3


def test_dashboard_no_phases_yet(client, auth_headers):
    _, headers = auth_headers

    resp = client.get(DASHBOARD_URL, headers=headers)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["phase_ring"] == {"current_phase": None, "total_phases": 0, "percent_complete": 0.0}
    assert body["last_butler_exchange"] == "No conversations yet."


def test_dashboard_unauthenticated(client):
    resp = client.get(DASHBOARD_URL)
    assert resp.status_code == 401


# --- test_phases -----------------------------------------------------------

def test_phases_returns_only_this_users_phases(client, auth_headers, fake_db, make_user):
    user, headers = auth_headers
    other_user, _ = make_user(email="other@example.com")

    fake_db.add_phase(user["id"], phase_number=2, phase_name="Build", status="active", estimated_days=20)
    fake_db.add_phase(user["id"], phase_number=1, phase_name="Foundation", status="complete", actual_days=7, estimated_days=14)
    fake_db.add_phase(other_user["id"], phase_number=1, phase_name="Someone Else's Phase")

    resp = client.get(PHASES_URL, headers=headers)

    assert resp.status_code == 200
    phases = resp.get_json()["phases"]
    assert len(phases) == 2
    # Sorted by phase_number ascending.
    assert [p["phase_name"] for p in phases] == ["Foundation", "Build"]
    # actual_days/estimated_days * 100 for the completed phase, computed exactly.
    assert phases[0]["progress_percent"] == 50.0


def test_phases_empty_for_new_user(client, auth_headers):
    _, headers = auth_headers
    resp = client.get(PHASES_URL, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["phases"] == []


def test_phases_unauthenticated(client):
    resp = client.get(PHASES_URL)
    assert resp.status_code == 401


# --- test_cors ---------------------------------------------------------

def test_cors_allowed_origin_is_reflected(client):
    resp = client.get(PROFILE_URL, headers={"Origin": FRONTEND_URL})
    assert resp.headers.get("Access-Control-Allow-Origin") == FRONTEND_URL


def test_cors_disallowed_origin_not_reflected(client):
    resp = client.get(PROFILE_URL, headers={"Origin": "https://evil.example.com"})
    assert resp.headers.get("Access-Control-Allow-Origin") != "https://evil.example.com"


def test_cors_preflight_allows_auth_header(client):
    resp = client.options(
        PROFILE_URL,
        headers={
            "Origin": FRONTEND_URL,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("Access-Control-Allow-Origin") == FRONTEND_URL
    allowed_headers = resp.headers.get("Access-Control-Allow-Headers", "")
    assert "Authorization" in allowed_headers
