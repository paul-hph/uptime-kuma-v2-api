"""Configuration loaded from environment variables."""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    # Kuma connection
    KUMA_SERVER = os.environ.get("KUMA_SERVER", "http://localhost:3001").rstrip("/")
    KUMA_USERNAME = os.environ.get("KUMA_USERNAME", "")
    KUMA_PASSWORD = os.environ.get("KUMA_PASSWORD", "")
    KUMA_TOTP_SECRET = os.environ.get("KUMA_TOTP_SECRET", "").strip() or None

    # Client auth (comma separated, strong random keys)
    API_KEYS = [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]

    # Timeout taxonomy (seconds)
    CONNECT_TIMEOUT = _int("CONNECT_TIMEOUT", 10)
    LOGIN_TIMEOUT = _int("LOGIN_TIMEOUT", 15)
    LOCK_ACQUIRE_TIMEOUT = _int("LOCK_ACQUIRE_TIMEOUT", 5)
    CALL_TIMEOUT = _int("CALL_TIMEOUT", 30)
    MUTATION_TIMEOUT = _int("MUTATION_TIMEOUT", 60)

    # Reconnect
    BACKOFF_CAP = _int("BACKOFF_CAP", 30)
    MAX_RECONNECT_TRIES = _int("MAX_RECONNECT_TRIES", 0)  # 0 = infinite

    # Cache
    CACHE_TTL = _int("CACHE_TTL", 30)

    # Max concurrent Kuma calls allowed into the threadpool (backpressure)
    MAX_CONCURRENT_CALLS = _int("MAX_CONCURRENT_CALLS", 16)
    SEM_ACQUIRE_TIMEOUT = _int("SEM_ACQUIRE_TIMEOUT", 5)

    # Rate limiting (slowapi syntax)
    RATE_LIMIT = os.environ.get("RATE_LIMIT", "120/minute")

    # Trusted proxy IPs for forwarded headers (uvicorn --forwarded-allow-ips)
    FORWARDED_ALLOW_IPS = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")


settings = Settings()
