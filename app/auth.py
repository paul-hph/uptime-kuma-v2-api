"""API-key authentication for clients (constant-time compare, multiple keys)."""
import secrets

from fastapi import Header, HTTPException

from .config import settings


def is_valid_key(value: str) -> bool:
    if not value:
        return False
    return any(secrets.compare_digest(value, k) for k in settings.API_KEYS)


async def require_api_key(x_api_key: str = Header(default="")):
    # async: pure CPU, must not consume a threadpool worker
    if not settings.API_KEYS:
        raise HTTPException(status_code=503, detail="server misconfigured: API_KEYS not set")
    if not x_api_key:
        raise HTTPException(status_code=401, detail="missing X-API-Key header")
    if not is_valid_key(x_api_key):
        raise HTTPException(status_code=403, detail="invalid API key")
    return True
