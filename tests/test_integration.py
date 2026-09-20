"""POST /conversation (with the Claude API mocked), full multi-endpoint flows,
and direct unit tests for the agents + butler edge cases HTTP-level tests
don't naturally reach (declining/bursty activity patterns, pathway overrun
thresholds, blocker stuck-type branches, prompt/message assembly).
"""
from datetime import datetime, timedelta, timezone

import butler
from agents import blocker_detector, enhancement_suggester, pathway_optimizer, performance_analyst

CONVERSATION_URL = "/api/v1/marquis/conversation"
LOGIN_URL = "/api/v1/marquis/auth/login"
SIGNUP_URL = "/api/v1/marquis/auth/signup"
PROFILE_URL = "/api/v1/marquis/user/profile"
PHASES_URL = "/api/v1/marquis/phases"
DASHBOARD_URL = "/api/v1/marquis/dashboard"


# --- test_conversation ---------------------------------------------------

def test_conversation_success_returns_butler_reply_and_agent_context(client, auth_headers, fake_db, mock_claude):
    user, headers = auth_headers
    fake_db.add_phase(user["id"], phase_name="Foundation", estimated_days=14)
    mock_claude["reply"] = "This approach will cost you six weeks."

    resp = client.post(CONVERSATION_URL, headers=headers, json={"message": "What should I build first?"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["butler_response"] == "This approach will cost you six weeks."
    assert set(body["agent_context"].keys()) == {"performance", "pathway", "blocker", "enhancement"}
    assert all(isinstance(v, str) and v for v in body["agent_context"].values())


def test_conversation_logs_both_turns_and_activity(client, auth_headers, fake_db, mock_claude):
    user, headers = auth_headers

    client.post(CONVERSATION_URL, headers=headers, json={"message": "Hello butler"})

    convo = [c for c in fake_db.conversations if c["user_id"] == user["id"]]
    assert [c["role"] for c in convo] == ["user", "butler"]
    assert convo[0]["content"] == "Hello butler"

    activity = [a for a in fake_db.activity_logs if a["event_type"] == "butler_interaction"]
    assert len(activity) == 1


def test_conversation_uses_conversation_history(client, auth_headers, mock_claude):
    _, headers = auth_headers
    history = [{"role": "user", "content": "earlier message"}, {"role": "assistant", "content": "earlier reply"}]

    resp = client.post(
        CONVERSATION_URL,
        headers=headers,
        json={"message": "follow-up question", "conversation_history": history},
    )

    assert resp.status_code == 200


def test_conversation_missing_message_returns_400(client, auth_headers, mock_claude):
    _, headers = auth_headers
    resp = client.post(CONVERSATION_URL, headers=headers, json={})
    assert resp.status_code == 400


def test_conversation_message_too_long_returns_400(client, auth_headers, mock_claude):
    _, headers = auth_headers
    from config import Config

    resp = client.post(
        CONVERSATION_URL, headers=headers, json={"message": "x" * (Config.MAX_MESSAGE_LENGTH + 1)}
    )
    assert resp.status_code == 400


def test_conversation_invalid_history_type_returns_400(client, auth_headers, mock_claude):
    _, headers = auth_headers
    resp = client.post(
        CONVERSATION_URL, headers=headers, json={"message": "hi", "conversation_history": "not-a-list"}
    )
    assert resp.status_code == 400


def test_conversation_claude_failure_returns_500(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["side_effect"] = RuntimeError("Anthropic API unreachable")

    resp = client.post(CONVERSATION_URL, headers=headers, json={"message": "hi"})

    assert resp.status_code == 500
    assert "error" in resp.get_json()


def test_conversation_unauthenticated_returns_401(client, mock_claude):
    resp = client.post(CONVERSATION_URL, json={"message": "hi"})
    assert resp.status_code == 401


# --- full multi-endpoint flow -------------------------------------------

def test_full_signup_to_conversation_flow(client, fake_db, mock_claude):
    """Exercises signup -> login -> profile -> phases -> dashboard -> conversation
    against the same fake backing store, as one coherent user journey."""
    signup_resp = client.post(
        SIGNUP_URL, json={"email": "journey@example.com", "password": "flow-password-1", "name": "Jo Founder"}
    )
    assert signup_resp.status_code == 201
    user_id = signup_resp.get_json()["user_id"]

    login_resp = client.post(LOGIN_URL, json={"email": "journey@example.com", "password": "flow-password-1"})
    assert login_resp.status_code == 200
    token = login_resp.get_json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    profile_resp = client.get(PROFILE_URL, headers=headers)
    assert profile_resp.status_code == 200
    assert profile_resp.get_json()["id"] == user_id

    fake_db.add_phase(user_id, phase_name="Foundation", estimated_days=14)

    phases_resp = client.get(PHASES_URL, headers=headers)
    assert phases_resp.status_code == 200
    assert len(phases_resp.get_json()["phases"]) == 1

    dashboard_resp = client.get(DASHBOARD_URL, headers=headers)
    assert dashboard_resp.status_code == 200
    assert dashboard_resp.get_json()["phase_ring"]["total_phases"] == 1

    convo_resp = client.post(CONVERSATION_URL, headers=headers, json={"message": "Where do I start?"})
    assert convo_resp.status_code == 200
    assert convo_resp.get_json()["butler_response"]


# --- performance_analyst: activity-pattern branches ------------------------

def _iso(days_ago: float, naive: bool = False) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.replace(tzinfo=None).isoformat() if naive else dt.isoformat()


def test_performance_analyst_no_active_phase(monkeypatch):
    monkeypatch.setattr(performance_analyst, "get_active_phase", lambda uid: None)
    monkeypatch.setattr(performance_analyst, "get_recent_activity", lambda uid, days=30: [])
    monkeypatch.setattr(performance_analyst, "get_recent_conversations", lambda uid, days=30: [])

    result = performance_analyst.run_performance_analyst("u1")

    assert result["key_insight"] == "No active phase found for this user."


def test_performance_analyst_declining_pattern_and_naive_timestamp(monkeypatch):
    phase = {"phase_name": "Build", "started_at": _iso(1, naive=True), "estimated_days": None}
    # Two distinct ISO weeks (3 weeks apart, so never colliding): a heavy
    # old week and a near-empty recent week - second_half < first_half * 0.5.
    activity = [{"created_at": _iso(21)} for _ in range(10)] + [{"created_at": _iso(0)}]
    monkeypatch.setattr(performance_analyst, "get_active_phase", lambda uid: phase)
    monkeypatch.setattr(performance_analyst, "get_recent_activity", lambda uid, days=30: activity)
    monkeypatch.setattr(performance_analyst, "get_recent_conversations", lambda uid, days=30: [])

    result = performance_analyst.run_performance_analyst("u1")

    assert result["activity_pattern"] == "declining"
    assert "dropped sharply" in result["key_insight"]


def test_performance_analyst_bursty_pattern(monkeypatch):
    activity = [{"created_at": _iso(20)}] + [{"created_at": _iso(3 + i * 0.01)} for i in range(10)]
    monkeypatch.setattr(performance_analyst, "get_active_phase", lambda uid: None)
    monkeypatch.setattr(performance_analyst, "get_recent_activity", lambda uid, days=30: activity)
    monkeypatch.setattr(performance_analyst, "get_recent_conversations", lambda uid, days=30: [])

    result = performance_analyst.run_performance_analyst("u1")

    assert result["activity_pattern"] == "bursty"


def test_performance_analyst_consistent_pattern(monkeypatch):
    activity = [{"created_at": _iso(20)}, {"created_at": _iso(19)}, {"created_at": _iso(3)}, {"created_at": _iso(2)}]
    monkeypatch.setattr(performance_analyst, "get_active_phase", lambda uid: None)
    monkeypatch.setattr(performance_analyst, "get_recent_activity", lambda uid, days=30: activity)
    monkeypatch.setattr(performance_analyst, "get_recent_conversations", lambda uid, days=30: [])

    result = performance_analyst.run_performance_analyst("u1")

    assert result["activity_pattern"] == "consistent"


def test_performance_analyst_over_estimate_key_insight(monkeypatch):
    phase = {"phase_name": "Build", "started_at": _iso(20), "estimated_days": 10}
    monkeypatch.setattr(performance_analyst, "get_active_phase", lambda uid: phase)
    monkeypatch.setattr(performance_analyst, "get_recent_activity", lambda uid, days=30: [{"created_at": _iso(1)}])
    monkeypatch.setattr(performance_analyst, "get_recent_conversations", lambda uid, days=30: [])

    result = performance_analyst.run_performance_analyst("u1")

    assert "over its 10-day estimate" in result["key_insight"]


def test_performance_analyst_ahead_and_on_pace(monkeypatch):
    monkeypatch.setattr(performance_analyst, "get_recent_activity", lambda uid, days=30: [{"created_at": _iso(1)}])
    monkeypatch.setattr(performance_analyst, "get_recent_conversations", lambda uid, days=30: [])

    ahead_phase = {"phase_name": "Build", "started_at": _iso(2), "estimated_days": 10}
    monkeypatch.setattr(performance_analyst, "get_active_phase", lambda uid: ahead_phase)
    ahead = performance_analyst.run_performance_analyst("u1")
    assert "tracking ahead" in ahead["key_insight"]

    on_pace_phase = {"phase_name": "Build", "started_at": _iso(7), "estimated_days": 10}
    monkeypatch.setattr(performance_analyst, "get_active_phase", lambda uid: on_pace_phase)
    on_pace = performance_analyst.run_performance_analyst("u1")
    assert "on pace with estimate" in on_pace["key_insight"]


# --- pathway_optimizer: overrun thresholds ----------------------------

def test_pathway_optimizer_no_active_phase(monkeypatch):
    monkeypatch.setattr(pathway_optimizer, "get_phases", lambda uid: [])
    result = pathway_optimizer.run_pathway_optimizer("u1")
    assert result["pathway_status"] == "on_track"


def test_pathway_optimizer_significant_change(monkeypatch):
    phases = [{"status": "active", "phase_name": "Build", "estimated_days": 10, "started_at": _iso(25, naive=True)}]
    monkeypatch.setattr(pathway_optimizer, "get_phases", lambda uid: phases)

    result = pathway_optimizer.run_pathway_optimizer("u1")

    assert result["pathway_status"] == "significant_change"
    assert result["urgency"] == "immediate"


def test_pathway_optimizer_needs_adjustment_by_ratio(monkeypatch):
    phases = [{"status": "active", "phase_name": "Build", "estimated_days": 10, "started_at": _iso(16)}]
    monkeypatch.setattr(pathway_optimizer, "get_phases", lambda uid: phases)

    result = pathway_optimizer.run_pathway_optimizer("u1")

    assert result["pathway_status"] == "needs_adjustment"
    assert result["urgency"] == "next_conversation"


def test_pathway_optimizer_needs_adjustment_by_history(monkeypatch):
    phases = [
        {"status": "active", "phase_name": "Launch", "estimated_days": 10, "started_at": None},
        {"status": "complete", "phase_name": "A", "estimated_days": 10, "actual_days": 15},
        {"status": "complete", "phase_name": "B", "estimated_days": 10, "actual_days": 20},
    ]
    monkeypatch.setattr(pathway_optimizer, "get_phases", lambda uid: phases)

    result = pathway_optimizer.run_pathway_optimizer("u1")

    assert result["pathway_status"] == "needs_adjustment"
    assert "underestimating" in result["recommendation"]


# --- blocker_detector: stuck-type branches ---------------------------

def test_blocker_detector_decision_stuck(monkeypatch):
    conversations = [{"role": "user", "content": "what should my pricing be"} for _ in range(3)]
    monkeypatch.setattr(blocker_detector, "get_recent_conversations", lambda uid, days=14: conversations)
    monkeypatch.setattr(blocker_detector, "get_recent_activity", lambda uid, days=14: [{"created_at": _iso(0)}])

    result = blocker_detector.run_blocker_detector("u1")

    assert result["stuck_type"] == "decision"
    assert "pricing" in result["likely_cause"]


def test_blocker_detector_momentum_stuck(monkeypatch):
    monkeypatch.setattr(blocker_detector, "get_recent_conversations", lambda uid, days=14: [])
    monkeypatch.setattr(blocker_detector, "get_recent_activity", lambda uid, days=14: [{"created_at": _iso(3, naive=True)}])

    result = blocker_detector.run_blocker_detector("u1")

    assert result["stuck_type"] == "momentum"


def test_blocker_detector_confused_stuck(monkeypatch):
    topics = ["hosting providers", "logo colors", "tax registration", "app store review times"]
    conversations = [
        {"role": "user", "content": topic, "created_at": _iso(0)} for topic in topics
    ]
    monkeypatch.setattr(blocker_detector, "get_recent_conversations", lambda uid, days=14: conversations)
    monkeypatch.setattr(blocker_detector, "get_recent_activity", lambda uid, days=14: [])

    result = blocker_detector.run_blocker_detector("u1")

    assert result["stuck_type"] == "confused"


def test_blocker_detector_none(monkeypatch):
    monkeypatch.setattr(blocker_detector, "get_recent_conversations", lambda uid, days=14: [])
    monkeypatch.setattr(blocker_detector, "get_recent_activity", lambda uid, days=14: [{"created_at": _iso(0)}])

    result = blocker_detector.run_blocker_detector("u1")

    assert result["stuck_type"] == "none"


# --- enhancement_suggester: match / already-mentioned / no-user ------------

def test_enhancement_suggester_returns_top_priority_match(monkeypatch):
    monkeypatch.setattr(
        enhancement_suggester, "get_user_by_id", lambda uid: {"business_type": "SaaS", "stage": "Foundation"}
    )
    monkeypatch.setattr(enhancement_suggester, "get_recent_conversations", lambda uid, days=90: [])

    result = enhancement_suggester.run_enhancement_suggester("u1")

    assert result["surface_now"] == "5-10 customer discovery calls"


def test_enhancement_suggester_skips_already_mentioned(monkeypatch):
    monkeypatch.setattr(
        enhancement_suggester, "get_user_by_id", lambda uid: {"business_type": "SaaS", "stage": "Foundation"}
    )
    monkeypatch.setattr(
        enhancement_suggester,
        "get_recent_conversations",
        lambda uid, days=90: [{"content": "we already did 5-10 customer discovery calls last month"}],
    )

    result = enhancement_suggester.run_enhancement_suggester("u1")

    assert result["surface_now"] == "Landing page with an email waitlist"


def test_enhancement_suggester_no_user(monkeypatch):
    monkeypatch.setattr(enhancement_suggester, "get_user_by_id", lambda uid: None)
    result = enhancement_suggester.run_enhancement_suggester("u1")
    assert result == {"surface_now": None, "reason": None}


# --- butler.py: prompt assembly, message building, Claude call -----------

def test_build_agent_context_fallback_texts():
    context = butler.build_agent_context(
        performance={"key_insight": None},
        pathway={"pathway_status": "on_track", "recommendation": None},
        blocker={"stuck_type": "hard", "likely_cause": None},
        enhancement={"surface_now": "Add error monitoring", "reason": "Standard at launch."},
    )
    assert context["performance"] == "No notable pattern yet."
    assert context["pathway"] == "Pathway is on_track."
    assert context["blocker"] == "Possible hard stuck signal."
    assert context["enhancement"] == "Add error monitoring — Standard at launch."


def test_build_system_prompt_includes_approved_improvements():
    prompt = butler.build_system_prompt(
        agent_context={"performance": "p", "pathway": "w", "blocker": "b", "enhancement": "e"},
        prompt_improvements=[{"suggestion": "Be more direct", "reason": "users found tone soft"}],
    )
    assert "AGENT CONTEXT:" in prompt
    assert "Approved behavior improvements to apply:" in prompt
    assert "Be more direct" in prompt


def test_build_system_prompt_matches_hardcoded_text_outside_placeholder():
    prompt = butler.build_system_prompt(
        agent_context={"performance": "p", "pathway": "w", "blocker": "b", "enhancement": "e"},
        prompt_improvements=[],
    )
    assert prompt.startswith(
        "You are the Marquis butler. A guide with the mindset of someone who has already "
        "built something significant."
    )
    assert "Never more than 3 paragraphs per response." in prompt


def test_build_messages_maps_butler_role_and_filters_invalid_entries():
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "butler", "content": "earlier answer"},
        {"role": "system", "content": "should be dropped"},
        "not-a-dict",
        {"role": "user", "content": 12345},
    ]

    messages = butler._build_messages(history, "new message")

    assert messages == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "new message"},
    ]


def test_build_messages_with_non_list_history():
    messages = butler._build_messages(None, "hello")
    assert messages == [{"role": "user", "content": "hello"}]


def test_get_client_is_a_singleton(monkeypatch):
    monkeypatch.setattr(butler, "_client", None)
    first = butler.get_client()
    second = butler.get_client()
    assert first is second


def test_get_butler_response_calls_claude_and_strips_reply(monkeypatch):
    class FakeContentBlock:
        text = "  Padded reply.  "

    class FakeMessage:
        content = [FakeContentBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            self.kwargs = kwargs
            return FakeMessage()

    class FakeAnthropicClient:
        def __init__(self):
            self.messages = FakeMessages()

    monkeypatch.setattr(butler, "get_client", lambda: FakeAnthropicClient())

    reply = butler.get_butler_response("hi", "system prompt", history=[{"role": "user", "content": "earlier"}])

    assert reply == "Padded reply."
