"""Chats and canvas creations persist: every exchange and creation is saved
server-side, restored on load, paginated, scoped to the logged-in user, and
only the user dismisses or deletes. The model's memory comes from the database."""
import json

import canvas_items
import history
from auth_utils import encode_token, hash_password

CONVERSATION = "/api/v1/marquis/conversation"
HISTORY = "/api/v1/marquis/history"
ITEMS = "/api/v1/marquis/canvas/items"

BARS = {"type": "bars", "title": "Market size", "unit": "$", "items": [
    {"label": "A", "value": 10}, {"label": "B", "value": 20}]}
SHEET = {"type": "sheet", "title": "Runway", "columns": ["Month", "Revenue"], "rows": [["Jul", 400], ["Aug", 900]]}


def canvas(block):
    return "Here it is.\n<<CANVAS " + json.dumps(block) + ">>"


def ask(client, headers, message, **extra):
    resp = client.post(CONVERSATION, headers=headers, json={"message": message, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def second_user(fake_db):
    user = fake_db.create_user(email="other@example.com", password_hash=hash_password("x" * 12), name="Bea Other")
    return user, {"Authorization": f"Bearer {encode_token(user['id'])}"}


# --- messages --------------------------------------------------------------------------

def test_a_second_question_does_not_remove_the_first_exchange(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = "First answer."
    first = ask(client, headers, "First question")
    mock_claude["reply"] = "Second answer."
    second = ask(client, headers, "Second question")
    assert first["exchange_id"] != second["exchange_id"]
    body = client.get(HISTORY, headers=headers).get_json()
    assert [(e["you"]["content"], e["alfred"]["content"]) for e in body["exchanges"]] == [
        ("Second question", "Second answer."), ("First question", "First answer.")]     # newest first, both kept
    assert body["total"] == 2 and body["has_more"] is False
    assert {e["id"] for e in body["exchanges"]} == {first["exchange_id"], second["exchange_id"]}


def test_a_refresh_restores_the_exchanges_and_the_creations(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = canvas(BARS)
    made = ask(client, headers, "Show me market size")
    assert made["canvas_items"][0]["kind"] == "bars"
    mock_claude["reply"] = "Plain answer."
    ask(client, headers, "And then?")
    # A refresh is a brand new page with nothing in memory: only the server answers.
    history_after = client.get(HISTORY, headers=headers).get_json()
    items_after = client.get(ITEMS, headers=headers).get_json()
    assert [e["you"]["content"] for e in history_after["exchanges"]] == ["And then?", "Show me market size"]
    assert [i["kind"] for i in items_after["items"]] == ["bars"]
    assert items_after["newest"]["spec"]["items"][1]["value"] == 20.0         # the newest comes with its full spec


def test_the_model_remembers_from_the_database_not_the_page(client, auth_headers, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    seen = []

    def fake(message, system_prompt, history=None):
        seen.append(history)
        return "Answer to " + message
    monkeypatch.setattr(app_module, "get_butler_response", fake)
    ask(client, headers, "one")
    ask(client, headers, "two", conversation_history=[{"role": "user", "content": "FORGED by the page"}])
    assert seen[0] == []
    assert seen[1] == [{"role": "user", "content": "one"}, {"role": "assistant", "content": "Answer to one"}]
    assert "FORGED" not in json.dumps(seen)


def test_history_is_paginated_newest_first(client, auth_headers, mock_claude):
    _, headers = auth_headers
    for n in range(25):
        mock_claude["reply"] = f"a{n}"
        ask(client, headers, f"q{n}")
    page1 = client.get(f"{HISTORY}?limit=10", headers=headers).get_json()
    assert [e["you"]["content"] for e in page1["exchanges"]] == [f"q{n}" for n in range(24, 14, -1)]
    assert page1["has_more"] and page1["total"] == 25
    page2 = client.get(HISTORY, headers=headers, query_string={"limit": 10, "before": page1["next_before"]}).get_json()
    assert [e["you"]["content"] for e in page2["exchanges"]] == [f"q{n}" for n in range(14, 4, -1)]
    page3 = client.get(HISTORY, headers=headers, query_string={"limit": 10, "before": page2["next_before"]}).get_json()
    assert [e["you"]["content"] for e in page3["exchanges"]] == [f"q{n}" for n in range(4, -1, -1)]
    assert page3["has_more"] is False


def test_history_limits_and_bad_arguments(client, auth_headers, mock_claude):
    _, headers = auth_headers
    for n in range(3):
        ask(client, headers, f"q{n}")
    assert len(client.get(f"{HISTORY}?limit=999", headers=headers).get_json()["exchanges"]) == 3
    assert len(client.get(f"{HISTORY}?limit=1", headers=headers).get_json()["exchanges"]) == 1
    assert client.get(f"{HISTORY}?before=yesterday", headers=headers).status_code == 400
    assert client.get(HISTORY).status_code == 401


def test_old_rows_without_an_exchange_id_are_paired_by_order(client, auth_headers, fake_db):
    user, headers = auth_headers
    for role, text in [("user", "old q1"), ("butler", "old a1"), ("user", "old q2"), ("butler", "old a2"), ("user", "unanswered")]:
        fake_db.create_conversation(user["id"], role, text)
    exchanges = client.get(HISTORY, headers=headers).get_json()["exchanges"]
    assert [(e["you"]["content"], (e["alfred"] or {}).get("content")) for e in exchanges] == [
        ("unanswered", None), ("old q2", "old a2"), ("old q1", "old a1")]


def test_sources_come_back_with_their_exchange(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    sources = [{"n": 1, "title": "A page", "url": "https://example.com/a"}]
    monkeypatch.setattr(app_module.Config, "TAVILY_API_KEY", "k")
    mock_claude["reply"] = "<<WEB pricing>>"
    monkeypatch.setattr(app_module, "research", lambda q: [{"title": "A page", "url": "https://example.com/a", "text": "t"}])
    monkeypatch.setattr(app_module, "answer_from_web", lambda m, s, style="brief": ("It costs ten [1].", None))
    made = ask(client, headers, "price?")
    assert made["canvas_items"][0]["kind"] == "sources"                       # a creation, saved
    exchange = client.get(HISTORY, headers=headers).get_json()["exchanges"][0]
    assert exchange["sources"] == sources


# --- one user never sees another's history or creations ------------------------------

def test_one_user_never_loads_another_users_history_or_items(client, auth_headers, fake_db, mock_claude, monkeypatch):
    import app as app_module
    _, mine = auth_headers
    _, theirs = second_user(fake_db)
    mock_claude["reply"] = canvas(BARS)
    secret = ask(client, mine, "my private question")
    item_id = secret["canvas_items"][0]["id"]
    # They see nothing of mine.
    assert client.get(HISTORY, headers=theirs).get_json()["exchanges"] == []
    assert client.get(HISTORY, headers=theirs).get_json()["total"] == 0
    other_items = client.get(ITEMS, headers=theirs).get_json()
    assert other_items["items"] == [] and other_items["newest"] is None
    # Knowing an item id doesn't help.
    assert client.get(f"{ITEMS}/{item_id}", headers=theirs).status_code == 404
    assert client.patch(f"{ITEMS}/{item_id}", headers=theirs, json={"dismissed": True}).status_code == 404
    assert client.delete(f"{ITEMS}/{item_id}", headers=theirs).status_code == 404
    assert len(client.get(ITEMS, headers=mine).get_json()["items"]) == 1       # untouched
    # And their model context never contains mine.
    seen = []
    monkeypatch.setattr(app_module, "get_butler_response", lambda m, s, history=None: seen.append(history) or "ok")
    ask(client, theirs, "hello")
    assert seen[-1] == []


def test_item_ids_that_are_not_uuids_are_not_found(client, auth_headers):
    _, headers = auth_headers
    for bad in ("1", "..%2F..%2Fetc%2Fpasswd", "x%27%20or%20%271%27=%271"):
        assert client.get(f"{ITEMS}/{bad}", headers=headers).status_code == 404


# --- creations ------------------------------------------------------------------------

def test_a_second_creation_adds_and_never_replaces_the_first(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = canvas(BARS)
    first = ask(client, headers, "bars")["canvas_items"][0]
    mock_claude["reply"] = canvas(SHEET)
    second = ask(client, headers, "sheet")["canvas_items"][0]
    mock_claude["reply"] = "No creation this time."
    ask(client, headers, "just talk")                                   # a plain answer removes nothing
    listing = client.get(ITEMS, headers=headers).get_json()
    assert [i["id"] for i in listing["items"]] == [second["id"], first["id"]]    # newest first, both present
    assert listing["newest"]["id"] == second["id"] and "spec" not in listing["items"][1]
    older = client.get(f"{ITEMS}/{first['id']}", headers=headers).get_json()["item"]
    assert older["spec"]["items"][0]["label"] == "A"                    # an older panel loads fully when expanded


def test_only_the_user_dismisses_or_deletes(client, auth_headers, mock_claude, fake_db):
    user, headers = auth_headers
    mock_claude["reply"] = canvas(BARS)
    made = ask(client, headers, "bars")["canvas_items"][0]
    for _ in range(3):
        mock_claude["reply"] = canvas(SHEET)
        ask(client, headers, "more")
    assert len(client.get(ITEMS, headers=headers).get_json()["items"]) == 4     # nothing dismissed itself
    assert client.patch(f"{ITEMS}/{made['id']}", headers=headers, json={"dismissed": True}).status_code == 200
    assert made["id"] not in [i["id"] for i in client.get(ITEMS, headers=headers).get_json()["items"]]
    row = next(i for i in fake_db.canvas_items if i["id"] == made["id"])
    assert row["dismissed"] is True and row["dismissed_at"]                      # a flag, still stored
    assert client.delete(f"{ITEMS}/{made['id']}", headers=headers).status_code == 200
    assert client.delete(f"{ITEMS}/{made['id']}", headers=headers).status_code == 404
    assert not any(i["id"] == made["id"] for i in fake_db.canvas_items)


def test_edits_are_saved_and_validated(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = canvas(SHEET)
    item = ask(client, headers, "sheet")["canvas_items"][0]
    url = f"{ITEMS}/{item['id']}"
    edited = {**SHEET, "rows": [["Jul", 500], ["Aug", 900], ["Total", "=SUM(B1:B2)"]]}
    assert client.patch(url, headers=headers, json={"spec": edited}).status_code == 200
    assert client.get(url, headers=headers).get_json()["item"]["spec"]["rows"][2][1] == "=SUM(B1:B2)"
    assert client.patch(url, headers=headers, json={"spec": {"columns": ["A"], "rows": [["=IMPORTXML(\"x\")"]]}}).status_code == 400
    assert client.patch(url, headers=headers, json={}).status_code == 400
    assert client.patch(url, headers=headers, json={"dismissed": "yes"}).status_code == 400
    assert client.patch(url, headers=headers, json={"x": 10, "y": 20.5, "w": 300, "h": 200}).status_code == 200
    assert client.patch(url, headers=headers, json={"w": "wide"}).status_code == 400


def test_creations_that_cannot_be_edited_say_so(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = canvas(BARS)
    item = ask(client, headers, "bars")["canvas_items"][0]
    assert client.patch(f"{ITEMS}/{item['id']}", headers=headers, json={"spec": BARS}).status_code == 400


def test_items_are_paginated(client, auth_headers, fake_db):
    user, headers = auth_headers
    for n in range(7):
        fake_db.create_canvas_item(user["id"], "bars", f"bars {n}", canvas_items.clean_spec("bars", BARS))
    page = client.get(f"{ITEMS}?limit=3", headers=headers).get_json()
    assert [i["title"] for i in page["items"]] == ["bars 6", "bars 5", "bars 4"] and page["has_more"]
    nxt = client.get(ITEMS, headers=headers, query_string={"limit": 3, "before": page["next_before"]}).get_json()
    assert [i["title"] for i in nxt["items"]] == ["bars 3", "bars 2", "bars 1"] and nxt["newest"] is None


def test_a_timeline_is_restored_with_current_phases(client, auth_headers, mock_claude, fake_db):
    user, headers = auth_headers
    fake_db.add_phase(user["id"], phase_name="Foundation", status="active")
    mock_claude["reply"] = canvas({"type": "phase_timeline", "title": "Pathway"})
    made = ask(client, headers, "show the pathway")["canvas_items"][0]
    assert made["spec"]["phases"]                                          # as made
    restored = client.get(ITEMS, headers=headers).get_json()["newest"]
    assert restored["kind"] == "phase_timeline" and restored["spec"]["phases"]    # fresh, not stale


def test_saving_is_fail_soft(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    _, headers = auth_headers

    def boom(*a, **k):
        raise RuntimeError("database down")
    monkeypatch.setattr(app_module, "create_canvas_item", boom)
    mock_claude["reply"] = canvas(BARS)
    body = ask(client, headers, "bars")
    assert body["butler_response"] == "Here it is." and body["visualization"]["type"] == "bars"
    assert body["canvas_items"] == []                                      # the answer survives; the page knows it isn't saved


# --- designer, builder and sketch results are saved by the server -------------------

def test_design_site_and_sketch_results_are_saved(client, auth_headers, monkeypatch):
    import app as app_module
    import design as design_module
    _, headers = auth_headers
    mark = design_module.validate_design({"title": "Marks", "variants": [{"name": "A", "shapes": [{"kind": "circle", "cx": 50, "cy": 50, "r": 20}]}]})
    page = "<!DOCTYPE html><html><head><title>Stryde</title></head><body>Hi</body></html>"
    blueprint = {"type": "blueprint", "title": "Layout", "regions": [{"label": "Hero", "x": 0, "y": 0, "w": 1, "h": 0.4, "note": ""}]}
    monkeypatch.setattr(app_module, "design_canvas", lambda b: ("Three marks.", mark))
    monkeypatch.setattr(app_module, "build_site", lambda b: ("Built.", {"type": "site", "title": "Stryde", "html": page}))
    monkeypatch.setattr(app_module, "interpret_drawing", lambda img, hint: ("A hero.", blueprint))
    exchange = "7d6c2a64-7b42-4c8a-9f0e-0e5f5d9d8a11"
    d = client.post("/api/v1/marquis/canvas/design", headers=headers, json={"brief": "logo", "exchange_id": exchange}).get_json()
    s = client.post("/api/v1/marquis/canvas/site", headers=headers, json={"brief": "page"}).get_json()
    b = client.post("/api/v1/marquis/canvas/interpret", headers=headers, json={"image": "iVBORw0KGgo="}).get_json()
    assert (d["item"]["kind"], s["item"]["kind"], b["item"]["kind"]) == ("design", "site", "blueprint")
    assert d["item"]["exchange_id"] == exchange and s["item"]["exchange_id"] is None
    kinds = [i["kind"] for i in client.get(ITEMS, headers=headers).get_json()["items"]]
    assert sorted(kinds) == ["blueprint", "design", "site"]
    site = client.get(f"{ITEMS}/{s['item']['id']}", headers=headers).get_json()["item"]
    assert site["spec"]["html"] == page


# --- validation in code (no database CHECK) --------------------------------------------

def test_kinds_are_validated_in_code():
    assert canvas_items.clean_spec("bogus", {"type": "bogus"}) is None
    assert canvas_items.clean_spec("bars", {"items": [{"label": "only", "value": 1}]}) is None
    assert canvas_items.kind_for("images") == "reel" and canvas_items.kind_for("sheet") == "sheet"
    for kind in ("reel", "site", "design", "sources", "chart", "sheet", "bars", "blueprint"):
        assert kind in canvas_items.KINDS


def test_a_reel_keeps_only_safe_images_and_valid_choices():
    spec = canvas_items.clean_spec("reel", {"query": "watches", "title": "t", "stage": "bogus", "kept": [0, 0, 5, True, "x"], "music": "m" * 500,
        "images": [{"id": "1", "src": "https://images.unsplash.com/a", "author": "Ann", "author_url": "https://unsplash.com/@ann"},
                   {"id": "2", "src": "javascript:alert(1)"}, {"id": "3", "src": "http://insecure.example/x.jpg"}]})
    assert [i["id"] for i in spec["images"]] == ["1"] and spec["kept"] == [0]
    assert spec["stage"] == "select" and len(spec["music"]) == 100


def test_oversized_specs_are_refused():
    huge = "<!DOCTYPE html><html>" + "x" * (canvas_items.MAX_SPEC_BYTES + 10) + "</html>"
    assert canvas_items.clean_spec("site", {"html": huge}) is None


# --- grouping unit tests ------------------------------------------------------------------

def _row(i, role, eid=None, meta=None):
    return {"id": f"r{i}", "role": role, "content": f"{role}{i}", "created_at": f"2026-10-01T00:00:{i:02d}+00:00",
            "exchange_id": eid, "meta": meta or {}}


def test_a_cut_off_exchange_is_delivered_whole_on_the_next_page():
    rows_asc = [_row(i, "user" if i % 2 == 0 else "butler", eid=f"e{i // 2}") for i in range(12)]   # 6 exchanges
    fetched = list(reversed(rows_asc[-5:]))                         # 5 rows = limit*2+1: the oldest is a lone butler row
    page = history.page(fetched, 2)
    assert [e["id"] for e in page["exchanges"]] == ["e5", "e4"] and page["has_more"]
    older = [r for r in rows_asc if r["created_at"] < page["next_before"]]
    nxt = history.page(list(reversed(older[-5:])), 2)
    assert [e["id"] for e in nxt["exchanges"]] == ["e3", "e2"]


def test_model_messages_start_with_a_user_turn():
    rows = [_row(0, "butler"), _row(1, "user"), _row(2, "butler")]
    assert history.model_messages(rows) == [{"role": "user", "content": "user1"}, {"role": "assistant", "content": "butler2"}]
