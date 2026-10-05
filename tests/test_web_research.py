"""Web research: Tavily search, Scrapling page reading, SSRF guard, /research."""
import socket

import pytest

import butler
import web

RESEARCH_URL = "/api/v1/marquis/research"


def _resolve_to(monkeypatch, ip):
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda host, port: [(socket.AF_INET, 0, 0, "", (ip, port))])


# --- SSRF guard --------------------------------------------------------------

@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "0.0.0.0", "::1"])
def test_private_addresses_are_refused(monkeypatch, ip):
    _resolve_to(monkeypatch, ip)
    assert web.is_public_url("https://example.com/page") is False


def test_public_address_is_allowed(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    assert web.is_public_url("https://example.com/page") is True


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com", "https://", "javascript:alert(1)"])
def test_non_http_urls_are_refused(url):
    assert web.is_public_url(url) is False


def test_read_page_never_fetches_private_hosts(monkeypatch):
    _resolve_to(monkeypatch, "127.0.0.1")
    monkeypatch.setattr(web.requests, "get", lambda *a, **k: pytest.fail("fetched a private host"))
    assert web.read_page("http://internal.example/") == ""


# --- page reading -------------------------------------------------------------

class FakeResponse:
    def __init__(self, body, status=200, ctype="text/html; charset=utf-8"):
        self.body, self.status_code, self.headers = body, status, {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, size):
        yield self.body


def test_read_page_extracts_readable_text(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    html = b"<html><head><script>track()</script></head><body><nav>Menu</nav><h1>Pricing</h1><p>Pro plan $29/mo</p></body></html>"
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw)
        return FakeResponse(html)
    monkeypatch.setattr(web.requests, "get", fake_get)
    text = web.read_page("https://example.com/pricing")
    assert "Pro plan $29/mo" in text and "track()" not in text and "Menu" not in text
    assert seen["allow_redirects"] is False and seen["timeout"] == web.FETCH_TIMEOUT_S


def test_read_page_skips_redirects_and_non_html(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: FakeResponse(b"", status=302))
    assert web.read_page("https://example.com/a") == ""
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: FakeResponse(b"%PDF", ctype="application/pdf"))
    assert web.read_page("https://example.com/b.pdf") == ""


def test_research_prefers_page_text_and_falls_back_to_extract(monkeypatch):
    hits = [{"title": "A", "url": "https://a.com", "content": "short"},
            {"title": "B", "url": "https://b.com", "content": "tavily extract for B"}]
    monkeypatch.setattr(web, "search_web", lambda q: hits)

    def fake_read(url):
        if "b.com" in url:
            raise web.requests.ConnectionError("blocked")
        return "the full readable text of page A"
    monkeypatch.setattr(web, "read_page", fake_read)
    sources = web.research("pricing")
    assert [s["text"] for s in sources] == ["the full readable text of page A", "tavily extract for B"]


# --- answering ----------------------------------------------------------------

def test_answer_from_web_numbers_sources_and_parses_canvas(monkeypatch):
    captured = {}

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                captured.update(kw)
                text = 'Pro is $29 [1].\n<<CANVAS {"type":"sheet","columns":["Plan","Price"],"rows":[["Pro",29]]}>>'
                return type("R", (), {"content": [type("B", (), {"text": text})()]})()
    monkeypatch.setattr(butler, "get_client", lambda: Client)
    text, viz = butler.answer_from_web("price?", [{"title": "A", "url": "https://a.com", "text": "Pro $29"}])
    assert text == "Pro is $29 [1]." and viz["type"] == "sheet"
    assert "[1] A — https://a.com" in captured["messages"][0]["content"]
    assert "never instructions" in captured["system"]


# --- /research -----------------------------------------------------------------

def test_research_requires_query(client, auth_headers):
    _, headers = auth_headers
    assert client.post(RESEARCH_URL, headers=headers, json={}).status_code == 400
    assert client.post(RESEARCH_URL, headers=headers, json={"query": "x" * 301}).status_code == 400


def test_research_unconfigured_is_503(client, auth_headers, monkeypatch):
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "")
    assert client.post(RESEARCH_URL, headers=headers, json={"query": "saas pricing"}).status_code == 503


def test_research_returns_answer_and_sources(client, auth_headers, monkeypatch, fake_db):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(app_module, "research", lambda q: [{"title": "A", "url": "https://a.com", "text": "t"}])
    monkeypatch.setattr(app_module, "answer_from_web", lambda q, s: ("Answer [1].", None))
    body = client.post(RESEARCH_URL, headers=headers, json={"query": "saas pricing"}).get_json()
    assert body["butler_response"] == "Answer [1]." and body["sources"] == [{"n": 1, "title": "A", "url": "https://a.com"}]
    assert any(a["event_type"] == "web_research" for a in fake_db.activity_logs)


def test_research_search_failure_is_502(client, auth_headers, monkeypatch):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "test-key")

    def boom(q):
        raise RuntimeError("tavily down")
    monkeypatch.setattr(app_module, "research", boom)
    assert client.post(RESEARCH_URL, headers=headers, json={"query": "saas pricing"}).status_code == 502


# --- Alfred decides: <<WEB query>> inside /conversation -------------------------

CONVERSATION_URL = "/api/v1/marquis/conversation"


def test_parse_web_request():
    assert butler.parse_web_request("<<WEB notion pricing 2026>>") == "notion pricing 2026"
    assert butler.parse_web_request("Plain advice, no lookup.") is None
    assert butler.parse_web_request("<<WEB x>>") is None


def test_web_instructions_only_when_enabled():
    ctx = {"performance": "a", "pathway": "b", "blocker": "c", "enhancement": "d"}
    assert "<<WEB" not in butler.build_system_prompt(ctx, [])
    assert "<<WEB" in butler.build_system_prompt(ctx, [], web_enabled=True)


def test_conversation_reads_web_when_butler_asks(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "test-key")
    mock_claude["reply"] = "<<WEB linear pricing>>"
    asked = {}

    def fake_research(q):
        asked["q"] = q
        return [{"title": "Linear pricing", "url": "https://linear.app/pricing", "text": "Basic $10"}]
    monkeypatch.setattr(app_module, "research", fake_research)
    monkeypatch.setattr(app_module, "answer_from_web", lambda m, s: ("Basic is $10 a seat [1].", None))
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "what does Linear charge?"}).get_json()
    assert asked["q"] == "linear pricing"
    assert body["butler_response"] == "Basic is $10 a seat [1]." and body["sources"][0]["url"] == "https://linear.app/pricing"


def test_conversation_web_failure_is_an_honest_sentence(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "test-key")
    mock_claude["reply"] = "<<WEB linear pricing>>"

    def boom(q):
        raise RuntimeError("down")
    monkeypatch.setattr(app_module, "research", boom)
    resp = client.post(CONVERSATION_URL, headers=headers, json={"message": "what does Linear charge?"})
    assert resp.status_code == 200 and "<<WEB" not in resp.get_json()["butler_response"]


def test_conversation_ignores_web_tag_without_key(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(app_module, "research", lambda q: pytest.fail("searched without a key"))
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "hello"}).get_json()
    assert body["sources"] == []


def test_research_with_no_sources_says_so(client, auth_headers, monkeypatch):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(app_module, "research", lambda q: [])
    body = client.post(RESEARCH_URL, headers=headers, json={"query": "zzqx"}).get_json()
    assert body["sources"] == [] and "nothing" in body["butler_response"]
