"""Sep 24 redesign: Living Canvas payload, onboarding endpoints, phase
completion, and the dashboard contract the frontend renders."""
import butler

CONVERSATION_URL = "/api/v1/marquis/conversation"
QUESTIONS_URL = "/api/v1/marquis/onboarding/questions"
PATHWAY_URL = "/api/v1/marquis/onboarding/pathway"
PROFILE = {"business_type": "SaaS / Software", "stage": "Launched", "description": "Invoicing for freelance designers."}

COVERAGE_TAG = '<<CANVAS {"type":"coverage","title":"Standard for your stage","items":[{"name":"Payments","value":100},{"name":"Errors","value":10},{"name":"Email","value":80}]}>>'


# --- parse_visualization ---------------------------------------------------

def test_parse_valid_payload_strips_tag():
    prose, viz = butler.parse_visualization("Two gaps remain.\n\nBoth are cheap.\n" + COVERAGE_TAG)
    assert prose == "Two gaps remain.\n\nBoth are cheap."
    assert viz["type"] == "coverage" and len(viz["items"]) == 3


def test_parse_no_tag_is_plain_turn():
    assert butler.parse_visualization("Nothing to draw here.") == ("Nothing to draw here.", None)


def test_parse_malformed_json_fails_soft_and_never_leaks_tag():
    prose, viz = butler.parse_visualization("Here it is.\n<<CANVAS {not json}>>")
    assert viz is None and "<<CANVAS" not in prose and prose == "Here it is."


def test_parse_unknown_type_rejected():
    _, viz = butler.parse_visualization('Hm.\n<<CANVAS {"type":"pie","slices":[1,2]}>>')
    assert viz is None


def test_parse_mid_text_tag_is_stripped_and_ignored():
    prose, viz = butler.parse_visualization('Before <<CANVAS {"type":"coverage"}>> after.')
    assert viz is None and "<<CANVAS" not in prose


def test_parse_shape_violations_rejected():
    bad_heat = '<<CANVAS {"type":"blocker_heat","topics":[{"name":"Pricing","days":[1,2,3]}]}>>'
    bad_rev = '<<CANVAS {"type":"revenue_projection","points":[{"label":"Jan","value":"lots"}]}>>'
    assert butler.parse_visualization("x\n" + bad_heat)[1] is None
    assert butler.parse_visualization("x\n" + bad_rev)[1] is None


def test_system_prompt_includes_canvas_instructions():
    prompt = butler.build_system_prompt({"performance": "a", "pathway": "b", "blocker": "c", "enhancement": "d"}, [])
    assert "<<CANVAS" in prompt and "never invent numbers" in prompt


# --- /conversation visualization --------------------------------------------

def test_conversation_returns_visualization(client, auth_headers, mock_claude, fake_db):
    user, headers = auth_headers
    mock_claude["reply"] = "Two gaps remain.\n" + COVERAGE_TAG
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "What am I missing?"}).get_json()
    assert body["butler_response"] == "Two gaps remain."
    assert body["visualization"]["type"] == "coverage"
    stored = [c for c in fake_db.conversations if c["role"] == "butler"][-1]
    assert "<<CANVAS" not in stored["content"]


def test_conversation_plain_turn_has_null_visualization(client, auth_headers, mock_claude):
    _, headers = auth_headers
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "hello"}).get_json()
    assert body["visualization"] is None


def test_phase_timeline_is_filled_from_real_phases(client, auth_headers, mock_claude, fake_db):
    user, headers = auth_headers
    fake_db.add_phase(user["id"], phase_number=1, phase_name="Foundation", status="complete", estimated_days=14, actual_days=19)
    fake_db.add_phase(user["id"], phase_number=2, phase_name="Build", status="active", estimated_days=21)
    mock_claude["reply"] = 'Here is the route.\n<<CANVAS {"type":"phase_timeline","title":"Your route"}>>'
    viz = client.post(CONVERSATION_URL, headers=headers, json={"message": "Show me"}).get_json()["visualization"]
    assert [p["name"] for p in viz["phases"]] == ["Foundation", "Build"]
    assert [p["status"] for p in viz["phases"]] == ["done", "current"]


def test_phase_timeline_without_phases_degrades_to_none(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = 'Route.\n<<CANVAS {"type":"phase_timeline"}>>'
    assert client.post(CONVERSATION_URL, headers=headers, json={"message": "x"}).get_json()["visualization"] is None


# --- onboarding --------------------------------------------------------------

def test_onboarding_questions_success_saves_profile(client, auth_headers, fake_db, monkeypatch):
    import app as app_module
    user, headers = auth_headers
    monkeypatch.setattr(app_module, "generate_clarifying_questions", lambda t, s, d: ["Who pays first?", "What do they use now?", "What have you shipped?"])
    resp = client.post(QUESTIONS_URL, headers=headers, json=PROFILE)
    assert resp.status_code == 200 and len(resp.get_json()["questions"]) == 3
    assert fake_db.users[user["id"]]["business_type"] == "SaaS / Software"


def test_onboarding_questions_validates_input(client, auth_headers):
    _, headers = auth_headers
    resp = client.post(QUESTIONS_URL, headers=headers, json={**PROFILE, "description": "  "})
    assert resp.status_code == 400


def test_onboarding_questions_generation_failure_is_502(client, auth_headers, monkeypatch):
    import app as app_module
    _, headers = auth_headers

    def boom(*a):
        raise ValueError("bad model output")
    monkeypatch.setattr(app_module, "generate_clarifying_questions", boom)
    assert client.post(QUESTIONS_URL, headers=headers, json=PROFILE).status_code == 502


def test_onboarding_pathway_creates_phase_one_and_completes_onboarding(client, auth_headers, fake_db, monkeypatch):
    import app as app_module
    user, headers = auth_headers
    pathway = {"assessment": "a", "this_week": "w", "phase": {"name": "Distribution", "estimated_days": 10, "actions": [], "tools": [], "success": "s"}}
    monkeypatch.setattr(app_module, "generate_pathway", lambda t, s, d, a: pathway)
    resp = client.post(PATHWAY_URL, headers=headers, json={**PROFILE, "answers": [{"question": "q", "answer": "a"}]})
    assert resp.status_code == 200 and resp.get_json()["phase"]["name"] == "Distribution"
    phases = fake_db.get_phases(user["id"])
    assert len(phases) == 1 and phases[0]["status"] == "active" and phases[0]["estimated_days"] == 10
    assert fake_db.users[user["id"]]["onboarding_complete"] is True


def test_onboarding_pathway_rejects_bad_answers(client, auth_headers):
    _, headers = auth_headers
    assert client.post(PATHWAY_URL, headers=headers, json={**PROFILE, "answers": "nope"}).status_code == 400


def test_generate_pathway_parses_model_json(monkeypatch):
    raw = 'Sure.\n{"assessment":"Honest read.","phase":{"name":"Foundation","estimated_days":12,"actions":["Talk to five"],"tools":[{"name":"Notion","cost":"$0"}],"success":"5 calls"},"this_week":"Write the sentence."}'

    class R:
        content = [type("B", (), {"text": raw})()]
    monkeypatch.setattr(butler, "get_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **k: R())})()})())
    out = butler.generate_pathway("SaaS", "Idea only", "desc", [])
    assert out["phase"]["estimated_days"] == 12 and out["phase"]["tools"][0]["cost"] == "$0"


def test_generate_questions_rejects_wrong_count(monkeypatch):
    import pytest

    class R:
        content = [type("B", (), {"text": '{"questions":["only one?"]}'})()]
    monkeypatch.setattr(butler, "get_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **k: R())})()})())
    with pytest.raises(ValueError):
        butler.generate_clarifying_questions("SaaS", "Idea only", "desc")


# --- phase completion ---------------------------------------------------------

def test_complete_active_phase_advances_next(client, auth_headers, fake_db):
    user, headers = auth_headers
    p1 = fake_db.add_phase(user["id"], phase_number=1, phase_name="Foundation", status="active", estimated_days=14)
    p2 = fake_db.add_phase(user["id"], phase_number=2, phase_name="Build", status="future", estimated_days=21, started_at=None)
    resp = client.post(f"/api/v1/marquis/phases/{p1['id']}/complete", headers=headers)
    assert resp.status_code == 200
    assert fake_db.phases[p1["id"]]["status"] == "complete" and fake_db.phases[p1["id"]]["actual_days"] >= 1
    assert fake_db.phases[p2["id"]]["status"] == "active" and resp.get_json()["next_phase_id"] == p2["id"]


def test_complete_non_active_phase_is_400(client, auth_headers, fake_db):
    user, headers = auth_headers
    p = fake_db.add_phase(user["id"], status="future")
    assert client.post(f"/api/v1/marquis/phases/{p['id']}/complete", headers=headers).status_code == 400


def test_complete_other_users_phase_is_404(client, auth_headers, fake_db, make_user):
    _, headers = auth_headers
    other, _ = make_user(email="other@example.com")
    p = fake_db.add_phase(other["id"], status="active")
    assert client.post(f"/api/v1/marquis/phases/{p['id']}/complete", headers=headers).status_code == 404


# --- agent signals + enhancement normalization -----------------------------

def test_signals_empty_for_new_user(client, auth_headers):
    _, headers = auth_headers
    body = client.get("/api/v1/marquis/agents/signals", headers=headers).get_json()
    assert body == {"activity": [], "phases": [], "blocker_topics": [], "coverage": []}


def test_signals_reflect_real_activity_and_topics(client, auth_headers, fake_db):
    user, headers = auth_headers
    fake_db.users[user["id"]].update({"business_type": "SaaS / Software", "stage": "Launched"})
    fake_db.add_phase(user["id"], phase_number=1, phase_name="Launch", status="active", estimated_days=14)
    for text in ("How should I set pricing?", "Pricing again, monthly or annual?", "Still unsure about pricing tiers"):
        fake_db.create_conversation(user["id"], "user", text)
    fake_db.create_activity_log(user["id"], "butler_interaction", {})
    body = client.get("/api/v1/marquis/agents/signals", headers=headers).get_json()
    assert body["activity"][-1] == 1.0
    assert body["blocker_topics"][0]["name"] == "Pricing" and body["blocker_topics"][0]["days"][-1] == 3
    assert body["phases"][0]["status"] == "current"
    assert len(body["coverage"]) >= 3


def test_enhancement_library_accepts_onboarding_labels():
    from enhancement_library import get_enhancements
    assert get_enhancements("SaaS / Software", "Launched") == get_enhancements("SaaS", "Launch") != []
