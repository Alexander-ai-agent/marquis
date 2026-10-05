"""Application configuration loaded from environment variables."""
import os

from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")


def _env(name: str, default: str = "") -> str:
    """Read an env var and strip stray whitespace/newlines from copy-pasted values."""
    return os.getenv(name, default).strip()


class Config:
    """Central configuration object for the Flask app."""

    SUPABASE_URL: str = _env("SUPABASE_URL")
    SUPABASE_KEY: str = _env("SUPABASE_KEY")

    ANTHROPIC_API_KEY: str = _env("ANTHROPIC_API_KEY")
    CLAUDE_MODEL: str = "claude-sonnet-4-6"

    JWT_SECRET: str = _env("JWT_SECRET")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_DAYS: int = 7

    # No trailing slash: compared byte-exact against the browser's Origin
    # header for CORS. No wildcard fallback — unset must fail startup.
    FRONTEND_URL: str = _env("FRONTEND_URL").rstrip("/")

    DEBUG: bool = _env("DEBUG", "false").lower() == "true"
    PORT: int = int(_env("PORT", "5000"))

    BCRYPT_ROUNDS: int = 12

    RATE_LIMIT: str = "100 per minute"

    # Optional: n8n webhook URLs. Left unset, the corresponding calls are
    # skipped (best-effort) rather than failing the request that triggered
    # them — neither is in the required endpoint list, so nothing in this
    # API depends on them being configured.
    N8N_PHASE_COMPLETE_URL: str = _env("N8N_PHASE_COMPLETE_URL")
    N8N_ONBOARDING_URL: str = _env("N8N_ONBOARDING_URL")

    MAX_MESSAGE_LENGTH: int = 4000

    # Optional: Unsplash image search for the canvas (`images` block).
    # Unset -> /images/search returns 503 and the canvas says so honestly.
    UNSPLASH_ACCESS_KEY: str = _env("UNSPLASH_ACCESS_KEY")

    # Optional: Tavily web search for /research (pages then read via Scrapling).
    # Unset -> /research returns 503.
    TAVILY_API_KEY: str = _env("TAVILY_API_KEY")

    # Optional: Fish Audio voice for Alfred (/voice). Unset key -> 503 and the
    # frontend falls back to browser speech. Voice is swappable without code:
    # default is "Brian British" from the Fish Audio library.
    FISH_AUDIO_API_KEY: str = _env("FISH_AUDIO_API_KEY") or _env("FISHAUDIO_API_KEY")
    FISH_VOICE_ID: str = _env("FISH_VOICE_ID", "65c0b8155c464a648161af8877404f11")
    MAX_SPEECH_CHARS: int = 1500

    # Largest drawing accepted by /canvas/interpret (base64 PNG, bytes).
    MAX_DRAWING_BYTES: int = 2_500_000

    # Every secret the app cannot run safely without.
    _REQUIRED = (
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "JWT_SECRET",
        "FRONTEND_URL",
        "ANTHROPIC_API_KEY",
    )

    @classmethod
    def validate(cls) -> None:
        """Raise RuntimeError if any required secret/setting is unset or malformed.

        SUPABASE_URL is checked for a plausible shape (not just non-empty):
        a blank, truncated, or protocol-less value passes a bare truthiness
        check but then makes every Supabase call hang instead of fail
        fast (see get_client() in supabase_client.py) — catching it here,
        at boot, turns a silent runtime hang into an immediate deploy
        failure with a clear message.
        """
        missing = [name for name in cls._REQUIRED if not getattr(cls, name)]
        if missing:
            raise RuntimeError(
                "Missing required environment variable(s): " + ", ".join(missing)
            )
        if not cls.SUPABASE_URL.startswith("https://") or ".supabase.co" not in cls.SUPABASE_URL:
            raise RuntimeError(
                "SUPABASE_URL does not look like a valid Supabase project URL "
                f"(got: {cls.SUPABASE_URL!r}). Expected https://<project-ref>.supabase.co — "
                "check Railway's Variables tab for a truncated value or stray whitespace."
            )
