"""The builder (/canvas/site), reply styles, bars, and forgiving canvas parsing."""
import json

import butler
import site_builder

CONVERSATION_URL = "/api/v1/marquis/conversation"
SITE_URL = "/api/v1/marquis/canvas/site"
PAGE = "<!DOCTYPE html><html><head><title>Stryde</title></head><body><h1>Run</h1></body></html>"


def tag(payload):
    return "Here it is.\n<<CANVAS " + json.dumps(payload) + ">>"


# --- reply styles ------------------------------------------------------------------

def test_reply_styles_shape_the_prompt():
    ctx = {"performance": "a", "pathway": "b", "blocker": "c", "enhancement": "d"}
    brief = butler.build_system_prompt(ctx, [], reply_style="brief")
    detailed = butler.build_system_prompt(ctx, [], reply_style="detailed")
    assert "under 60 words" in brief and "paragraphs" in detailed
    assert "never code" in brief and "<<BUILD" in brief
    assert "under 60 words" in butler.build_system_prompt(ctx, [], reply_style="nonsense")


def test_conversation_passes_reply_style(client, auth_headers, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    seen = {}

    def fake(message, system_prompt, history=None):
        seen["prompt"] = system_prompt
        return "Fine."
    monkeypatch.setattr(app_module, "get_butler_response", fake)
    client.post(CONVERSATION_URL, headers=headers, json={"message": "hi", "reply_style": "detailed"})
    assert "paragraphs" in seen["prompt"]


# --- the builder hand-off -----------------------------------------------------------

def test_split_build_request():
    prose, brief = butler.split_build_request("A landing page, then.\n<<BUILD Stryde landing page, dark, bold>>")
    assert prose == "A landing page, then." and brief == "Stryde landing page, dark, bold"


def test_conversation_returns_build_brief(client, auth_headers, mock_claude):
    _, headers = auth_headers
    mock_claude["reply"] = "I'll build it now.\n<<BUILD Demo site for Stryde: hero, features, signup>>"
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "make a demo website"}).get_json()
    assert body["build"] == {"brief": "Demo site for Stryde: hero, features, signup"}
    assert body["design"] is None and body["butler_response"] == "I'll build it now."


def test_clean_html_extracts_the_document():
    raw = "<!-- SAID: Built. -->\n```html\n" + PAGE + "\n```"
    assert site_builder.clean_html(raw) == PAGE
    assert site_builder.clean_html("no html here") is None
    assert site_builder.clean_html("<html><body>unterminated") is None


def test_build_site_reads_said_and_title(monkeypatch):
    raw = "<!-- SAID: A page for Stryde, built to run. -->\n" + PAGE

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                return type("R", (), {"content": [type("B", (), {"type": "text", "text": raw})()]})()
    monkeypatch.setattr(site_builder, "get_client", lambda: Client)
    said, site = site_builder.build_site("stryde")
    assert said == "A page for Stryde, built to run." and site["title"] == "Stryde" and site["html"] == PAGE


def test_site_route(client, auth_headers, monkeypatch, fake_db):
    import app as app_module
    _, headers = auth_headers
    monkeypatch.setattr(app_module, "build_site", lambda b: ("Built.", {"type": "site", "title": "Stryde", "html": PAGE}))
    body = client.post(SITE_URL, headers=headers, json={"brief": "stryde landing"}).get_json()
    assert body["visualization"]["html"] == PAGE
    assert any(a["event_type"] == "site_built" for a in fake_db.activity_logs)
    assert client.post(SITE_URL, headers=headers, json={}).status_code == 400
    monkeypatch.setattr(app_module, "build_site", lambda b: ("", None))
    assert client.post(SITE_URL, headers=headers, json={"brief": "x"}).status_code == 422

    def boom(b):
        raise RuntimeError("down")
    monkeypatch.setattr(app_module, "build_site", boom)
    assert client.post(SITE_URL, headers=headers, json={"brief": "x"}).status_code == 502


# --- canvas: bars, gaps, and a tag that isn't last ------------------------------------

def test_bars_block():
    _, viz = butler.parse_visualization(tag({"type": "bars", "unit": "$", "items": [
        {"label": "Report A", "value": 258.9, "note": "2028"}, {"label": "Report B", "value": 695}]}))
    assert viz["type"] == "bars" and viz["items"][0]["note"] == "2028" and viz["items"][1]["value"] == 695.0
    assert butler.parse_visualization(tag({"type": "bars", "items": [{"label": "only", "value": 1}]}))[1] is None


def test_chart_with_gaps_keeps_complete_points():
    _, viz = butler.parse_visualization(tag({"type": "chart", "x": ["2022", "2024", "2026", "2028"],
                                             "series": [{"name": "Market", "values": [120, None, 190, 258.9]}]}))
    assert viz["x"] == ["2022", "2026", "2028"] and viz["series"][0]["values"] == [120.0, 190.0, 258.9]


def test_canvas_tag_followed_by_words_still_counts():
    reply = 'Here.\n<<CANVAS {"type":"bars","items":[{"label":"A","value":1},{"label":"B","value":2}]}>>\nShall I go on?'
    prose, viz = butler.parse_visualization(reply)
    assert viz["type"] == "bars" and "<<CANVAS" not in prose and "Shall I go on?" in prose
