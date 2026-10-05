"""Alfred's spoken voice via Fish Audio text-to-speech.

The key stays server-side; the frontend posts text to /voice and plays the
MP3. Recent lines are cached in memory (bounded) so a repeated greeting
costs nothing. If this fails the frontend falls back to the browser's own
speech, so Alfred is never silent.
"""
import hashlib
from collections import OrderedDict

import requests

from config import Config

FISH_TTS = "https://api.fish.audio/v1/tts"
TIMEOUT_S = 30
CACHE_LIMIT = 200
SPEED = 0.95  # unhurried, as a butler speaks

_cache: "OrderedDict[str, bytes]" = OrderedDict()


def synthesize(text: str) -> bytes:
    """Return MP3 bytes of `text` spoken in the configured Fish Audio voice."""
    key = hashlib.sha256(f"{Config.FISH_MODEL}:{Config.FISH_VOICE_ID}:{text}".encode()).hexdigest()
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    res = requests.post(
        FISH_TTS,
        json={
            "text": text,
            "reference_id": Config.FISH_VOICE_ID,
            "format": "mp3",
            "latency": "balanced",
            "normalize": True,
            "prosody": {"speed": SPEED},
        },
        headers={"Authorization": f"Bearer {Config.FISH_AUDIO_API_KEY}", "model": Config.FISH_MODEL},
        timeout=TIMEOUT_S,
    )
    res.raise_for_status()
    audio = res.content
    if not audio:
        raise ValueError("empty audio")
    _cache[key] = audio
    if len(_cache) > CACHE_LIMIT:
        _cache.popitem(last=False)
    return audio
