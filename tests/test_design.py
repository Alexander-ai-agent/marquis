"""The designer: brief hand-off, design validation, and /canvas/design."""
import json

import butler
import design

DESIGN_URL = "/api/v1/marquis/canvas/design"
CONVERSATION_URL = "/api/v1/marquis/conversation"

MARK = {
    "title": "Stryde marks",
    "variants": [
        {"name": "Monogram", "note": "An S cut from one stride", "background": "ground", "shapes": [
            {"kind": "circle", "cx": 50, "cy": 50, "r": 34, "stroke": "gold", "sw": 1.2},
            {"kind": "path", "d": "M 36 62 C 36 40, 64 60, 64 38", "stroke": "gold-gradient", "sw": 4},
            {"kind": "text", "x": 50, "y": 94, "text": "STRYDE", "size": 6, "family": "mono", "ls": 3},
        ]},
        {"name": "Facet", "shapes": [{"kind": "polygon", "points": [[50, 14], [86, 50], [50, 86], [14, 50]], "fill": "dusk-gradient"}]},
    ],
}


# --- hand-off from the butler ---------------------------------------------------

def test_split_design_request():
    prose, brief = butler.split_design_request("A stride, abstracted.\n<<DESIGN Logo for Stryde: geometric S>>")
    assert prose == "A stride, abstracted." and brief == "Logo for Stryde: geometric S"
    assert butler.split_design_request("No design here.") == ("No design here.", None)


def test_prompt_has_designer_and_spoken_rules():
    prompt = butler.build_system_prompt({"performance": "a", "pathway": "b", "blocker": "c", "enhancement": "d"}, [])
    assert "<<DESIGN" in prompt and "no markdown" in prompt


def test_conversation_returns_design_brief(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = "Three directions, all geometric.\n<<DESIGN Logo for Stryde, geometric, forward motion>>"
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "design a logo for stryde"}).get_json()
    assert body["design"] == {"brief": "Logo for Stryde, geometric, forward motion"}
    assert body["butler_response"] == "Three directions, all geometric." and body["visualization"] is None


def test_conversation_without_design_has_none(client, auth_headers, mock_claude):
    _, headers = auth_headers
    assert client.post(CONVERSATION_URL, headers=headers, json={"message": "hi"}).get_json()["design"] is None


# --- validation -------------------------------------------------------------------

def test_valid_design_is_kept():
    viz = design.validate_design(MARK)
    assert viz["type"] == "design" and len(viz["variants"]) == 2
    first = viz["variants"][0]["shapes"]
    assert first[1]["d"].startswith("M 36") and first[1]["stroke"] == "gold-gradient"
    assert first[2]["family"] == "mono" and first[2]["fill"] == "ink"
    assert viz["variants"][1]["background"] == "ground"


def test_malformed_shapes_are_dropped_not_fatal():
    viz = design.validate_design({"variants": [{"shapes": [
        {"kind": "path", "d": "M0 0 L10 10\"/><script>"},
        {"kind": "script"},
        {"kind": "rect", "x": 10, "y": 10, "w": 0, "h": 5},
        {"kind": "circle", "cx": 50, "cy": 50, "r": 10, "fill": "url(javascript:x)"},
    ]}]})
    shapes = viz["variants"][0]["shapes"]
    assert len(shapes) == 1 and shapes[0]["kind"] == "circle" and shapes[0]["fill"] == "none"


def test_empty_or_bad_design_is_none():
    assert design.validate_design(None) is None
    assert design.validate_design({"variants": []}) is None
    assert design.validate_design({"variants": [{"shapes": [{"kind": "nope"}]}]}) is None


def test_variants_and_shapes_are_capped():
    many = {"variants": [{"shapes": [{"kind": "circle", "cx": 50, "cy": 50, "r": 5}] * 100}] * 6}
    viz = design.validate_design(many)
    assert len(viz["variants"]) == design.MAX_VARIANTS
    assert len(viz["variants"][0]["shapes"]) == design.MAX_SHAPES_PER_VARIANT


def test_rotation_defaults_to_board_centre():
    viz = design.validate_design({"variants": [{"shapes": [{"kind": "rect", "x": 40, "y": 40, "w": 20, "h": 20, "rotate": 45}]}]})
    s = viz["variants"][0]["shapes"][0]
    assert s["rotate"] == 45 and s["ox"] == 50 and s["oy"] == 50


def test_design_falls_back_to_conversation_model(monkeypatch):
    used = []

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                used.append(kw["model"])
                if kw["model"] == "strong-model":
                    raise RuntimeError("model not available")
                return type("R", (), {"content": [type("B", (), {"text": json.dumps({"said": "x", "design": MARK})})()]})()
    monkeypatch.setattr(design, "get_client", lambda: Client)
    monkeypatch.setattr(design.Config, "DESIGN_MODEL", "strong-model")
    monkeypatch.setattr(design.Config, "CLAUDE_MODEL", "base-model")
    _, viz = design.design_canvas("logo")
    assert used == ["strong-model", "base-model"] and viz["type"] == "design"


def test_design_canvas_parses_model_json(monkeypatch):
    raw = json.dumps({"said": "A stride, held in a circle.", "design": MARK})

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                return type("R", (), {"content": [type("B", (), {"text": raw})()]})()
    monkeypatch.setattr(design, "get_client", lambda: Client)
    said, viz = design.design_canvas("logo for stryde")
    assert said.startswith("A stride") and viz["variants"][0]["name"] == "Monogram"


# --- /canvas/design ------------------------------------------------------------------

def test_design_route_returns_composition(client, auth_headers, monkeypatch, fake_db):
    import app as app_module
    _, headers = auth_headers
    monkeypatch.setattr(app_module, "design_canvas", lambda b: ("Three directions.", design.validate_design(MARK)))
    body = client.post(DESIGN_URL, headers=headers, json={"brief": "logo for stryde"}).get_json()
    assert body["visualization"]["type"] == "design" and body["butler_response"] == "Three directions."
    assert any(a["event_type"] == "design_drafted" for a in fake_db.activity_logs)


def test_design_route_validates_and_degrades(client, auth_headers, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    assert client.post(DESIGN_URL, headers=headers, json={}).status_code == 400
    assert client.post(DESIGN_URL, headers=headers, json={"brief": "x" * 401}).status_code == 400
    monkeypatch.setattr(app_module, "design_canvas", lambda b: ("", None))
    assert client.post(DESIGN_URL, headers=headers, json={"brief": "logo"}).status_code == 422

    def boom(b):
        raise RuntimeError("model down")
    monkeypatch.setattr(app_module, "design_canvas", boom)
    assert client.post(DESIGN_URL, headers=headers, json={"brief": "logo"}).status_code == 502
