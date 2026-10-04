"""General canvas blocks (sheet, chart, drawing, blueprint, images), the
drawing-interpretation endpoint, and image search."""
import json

import butler

CONVERSATION_URL = "/api/v1/marquis/conversation"
INTERPRET_URL = "/api/v1/marquis/canvas/interpret"
IMAGES_URL = "/api/v1/marquis/images/search"


def tag(payload):
    return "Here it is.\n<<CANVAS " + json.dumps(payload) + ">>"


# --- validation -------------------------------------------------------------

def test_sheet_with_formulas_is_accepted():
    _, viz = butler.parse_visualization(tag({"type": "sheet", "title": "Runway", "columns": ["Month", "Revenue"],
                                             "rows": [["Jul", 400], ["Aug", 900], ["Total", "=SUM(B1:B2)"]]}))
    assert viz["type"] == "sheet" and viz["rows"][2][1] == "=SUM(B1:B2)" and viz["rows"][0][1] == 400.0


def test_sheet_rejects_unsafe_formula_and_ragged_rows():
    bad_formula = {"type": "sheet", "columns": ["A"], "rows": [["=IMPORTXML(\"x\")"]]}
    too_wide = {"type": "sheet", "columns": ["A"], "rows": [[1, 2]]}
    assert butler.parse_visualization(tag(bad_formula))[1] is None
    assert butler.parse_visualization(tag(too_wide))[1] is None


def test_short_sheet_rows_are_padded():
    _, viz = butler.parse_visualization(tag({"type": "sheet", "columns": ["A", "B", "C"], "rows": [[1]]}))
    assert viz["rows"] == [[1.0, "", ""]]


def test_chart_series_must_match_x():
    ok = {"type": "chart", "x": ["Q1", "Q2", "Q3"], "series": [{"name": "Revenue", "values": [1, 2, 3], "projected_from": 2}]}
    bad = {"type": "chart", "x": ["Q1", "Q2"], "series": [{"name": "Revenue", "values": [1, 2, 3]}]}
    viz = butler.parse_visualization(tag(ok))[1]
    assert viz["series"][0]["projected_from"] == 2
    assert butler.parse_visualization(tag(bad))[1] is None


def test_drawing_shapes_validated_on_the_board():
    ok = {"type": "drawing", "shapes": [{"kind": "rect", "x": 10, "y": 10, "w": 30, "h": 20, "label": "Hero"},
                                        {"kind": "arrow", "x1": 40, "y1": 20, "x2": 70, "y2": 20},
                                        {"kind": "text", "x": 5, "y": 90, "text": "footer"}]}
    off_board = {"type": "drawing", "shapes": [{"kind": "circle", "x": 50, "y": 50, "r": 400}]}
    unknown = {"type": "drawing", "shapes": [{"kind": "polygon", "points": []}]}
    assert len(butler.parse_visualization(tag(ok))[1]["shapes"]) == 3
    assert butler.parse_visualization(tag(off_board))[1] is None
    assert butler.parse_visualization(tag(unknown))[1] is None


def test_blueprint_regions_normalized():
    ok = {"type": "blueprint", "regions": [{"label": "Nav", "x": 0, "y": 0, "w": 1, "h": 0.1}]}
    bad = {"type": "blueprint", "regions": [{"label": "Nav", "x": 0, "y": 0, "w": 2, "h": 0.1}]}
    assert butler.parse_visualization(tag(ok))[1]["regions"][0]["label"] == "Nav"
    assert butler.parse_visualization(tag(bad))[1] is None


def test_images_block_needs_a_query():
    assert butler.parse_visualization(tag({"type": "images", "query": "matte black watches"}))[1]["query"] == "matte black watches"
    assert butler.parse_visualization(tag({"type": "images", "query": "  "}))[1] is None


def test_prompt_describes_general_blocks():
    prompt = butler.build_system_prompt({"performance": "a", "pathway": "b", "blocker": "c", "enhancement": "d"}, [])
    assert '"type":"sheet"' in prompt and '"type":"drawing"' in prompt and "never invent numbers" in prompt


def test_conversation_returns_general_block(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = tag({"type": "blueprint", "regions": [{"label": "Hero", "x": 0, "y": 0, "w": 1, "h": 0.5}]})
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "lay out my page"}).get_json()
    assert body["visualization"]["type"] == "blueprint" and body["butler_response"] == "Here it is."


# --- /canvas/interpret --------------------------------------------------------

def test_interpret_returns_blueprint(client, auth_headers, monkeypatch, fake_db):
    import app as app_module
    _, headers = auth_headers
    bp = {"type": "blueprint", "title": "Landing", "regions": [{"label": "Hero", "x": 0, "y": 0, "w": 1, "h": 0.4, "note": ""}]}
    monkeypatch.setattr(app_module, "interpret_drawing", lambda img, hint: ("A hero above three columns.", bp))
    resp = client.post(INTERPRET_URL, headers=headers, json={"image": "data:image/png;base64,iVBORw0KGgo=", "hint": "a landing page"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["visualization"]["regions"][0]["label"] == "Hero" and "hero" in body["butler_response"].lower()
    assert any(a["event_type"] == "drawing_interpreted" for a in fake_db.activity_logs)


def test_interpret_validates_input(client, auth_headers):
    _, headers = auth_headers
    assert client.post(INTERPRET_URL, headers=headers, json={}).status_code == 400
    assert client.post(INTERPRET_URL, headers=headers, json={"image": "data:image/jpeg;base64,AAAA"}).status_code == 400


def test_interpret_unreadable_drawing_is_422(client, auth_headers, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    monkeypatch.setattr(app_module, "interpret_drawing", lambda img, hint: ("", None))
    assert client.post(INTERPRET_URL, headers=headers, json={"image": "iVBORw0KGgo="}).status_code == 422


def test_interpret_model_failure_is_502(client, auth_headers, monkeypatch):
    import app as app_module
    _, headers = auth_headers

    def boom(img, hint):
        raise ValueError("bad output")
    monkeypatch.setattr(app_module, "interpret_drawing", boom)
    assert client.post(INTERPRET_URL, headers=headers, json={"image": "iVBORw0KGgo="}).status_code == 502


def test_interpret_drawing_parses_model_json(monkeypatch):
    raw = '{"understood": "Two columns under a header.", "title": "Page", "regions": [{"label": "Header", "x": 0, "y": 0, "w": 1, "h": 0.2}]}'

    class R:
        content = [type("B", (), {"text": raw})()]
    monkeypatch.setattr(butler, "get_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **k: R())})()})())
    understood, bp = butler.interpret_drawing("iVBORw0KGgo=", "")
    assert understood.startswith("Two columns") and bp["regions"][0]["label"] == "Header"


# --- /images/search --------------------------------------------------------------

def test_images_unconfigured_is_503(client, auth_headers, monkeypatch):
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "UNSPLASH_ACCESS_KEY", "")
    assert client.get(IMAGES_URL + "?q=watches", headers=headers).status_code == 503


def test_images_search_returns_results(client, auth_headers, monkeypatch):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "UNSPLASH_ACCESS_KEY", "test-key")
    monkeypatch.setattr(app_module, "search_images", lambda q: [{"id": "1", "src": "https://images.example/1.jpg", "author": "A"}])
    body = client.get(IMAGES_URL + "?q=watches", headers=headers).get_json()
    assert body["images"][0]["src"].startswith("https://")


def test_images_requires_query(client, auth_headers):
    _, headers = auth_headers
    assert client.get(IMAGES_URL, headers=headers).status_code == 400
