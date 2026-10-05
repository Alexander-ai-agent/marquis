"""Alfred's voice: Fish Audio synthesis, caching, and the /voice route."""
import pytest

import voice

VOICE_URL = "/api/v1/marquis/voice"


class FakeResp:
    def __init__(self, content=b"ID3audio", status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise voice.requests.HTTPError(str(self.status_code))


@pytest.fixture(autouse=True)
def clear_cache():
    voice._cache.clear()
    yield
    voice._cache.clear()


def test_synthesize_sends_voice_and_caches(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers))
        return FakeResp()
    monkeypatch.setattr(voice.requests, "post", fake_post)
    monkeypatch.setattr(voice.Config, "FISH_AUDIO_API_KEY", "k")
    monkeypatch.setattr(voice.Config, "FISH_VOICE_ID", "v1")
    assert voice.synthesize("Good evening.") == b"ID3audio"
    assert voice.synthesize("Good evening.") == b"ID3audio"
    assert len(calls) == 1
    url, body, headers = calls[0]
    assert url == voice.FISH_TTS and body["reference_id"] == "v1" and body["format"] == "mp3"
    assert headers["Authorization"] == "Bearer k"


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(voice.requests, "post", lambda *a, **k: FakeResp())
    monkeypatch.setattr(voice, "CACHE_LIMIT", 3)
    for i in range(5):
        voice.synthesize(f"line {i}")
    assert len(voice._cache) == 3


def test_failure_raises_and_is_not_cached(monkeypatch):
    monkeypatch.setattr(voice.requests, "post", lambda *a, **k: FakeResp(status=402))
    with pytest.raises(voice.requests.HTTPError):
        voice.synthesize("x")
    assert not voice._cache


def test_voice_route_returns_mp3(client, auth_headers, monkeypatch):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    monkeypatch.setattr(Config, "FISH_AUDIO_API_KEY", "k")
    monkeypatch.setattr(app_module, "synthesize", lambda t: b"ID3audio")
    resp = client.post(VOICE_URL, headers=headers, json={"text": "Good evening."})
    assert resp.status_code == 200 and resp.mimetype == "audio/mpeg" and resp.data == b"ID3audio"


def test_voice_route_validates_and_degrades(client, auth_headers, monkeypatch):
    import app as app_module
    from config import Config
    _, headers = auth_headers
    assert client.post(VOICE_URL, headers=headers, json={}).status_code == 400
    assert client.post(VOICE_URL, headers=headers, json={"text": "x" * 1501}).status_code == 400
    monkeypatch.setattr(Config, "FISH_AUDIO_API_KEY", "")
    assert client.post(VOICE_URL, headers=headers, json={"text": "hi"}).status_code == 503
    monkeypatch.setattr(Config, "FISH_AUDIO_API_KEY", "k")

    def boom(t):
        raise RuntimeError("fish down")
    monkeypatch.setattr(app_module, "synthesize", boom)
    assert client.post(VOICE_URL, headers=headers, json={"text": "hi"}).status_code == 502


def test_voice_route_requires_auth(client):
    assert client.post(VOICE_URL, json={"text": "hi"}).status_code == 401
