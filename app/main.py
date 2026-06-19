"""Uptime Kuma V2 API — FastAPI wrapper around Kuma's socket.io interface."""
import asyncio
import functools
import hashlib
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import __version__
from .auth import is_valid_key, require_api_key
from .config import settings
from .kuma_client import (
    KumaClient, KumaError, KumaNotFound, KumaTimeout, KumaUnavailable,
)
from .monitors import defaults
from .monitors.schemas import (
    ActionResult, BeatsOut, CreateResult, MonitorCreate, MonitorListOut, MonitorOut, MonitorPatch,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")

client: KumaClient | None = None
_sem: asyncio.Semaphore | None = None


def _rate_key(request: Request) -> str:
    # Codex P1-5: never key on raw unvalidated header (rotating fake keys bypass limits).
    key = request.headers.get("x-api-key", "")
    if is_valid_key(key):
        return "key:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    return "ip:" + get_remote_address(request)


limiter = Limiter(key_func=_rate_key, default_limits=[settings.RATE_LIMIT])


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, _sem
    _sem = asyncio.Semaphore(settings.MAX_CONCURRENT_CALLS)
    client = KumaClient(
        settings.KUMA_SERVER, settings.KUMA_USERNAME, settings.KUMA_PASSWORD,
        totp=settings.KUMA_TOTP_SECRET,
        connect_timeout=settings.CONNECT_TIMEOUT, login_timeout=settings.LOGIN_TIMEOUT,
        lock_timeout=settings.LOCK_ACQUIRE_TIMEOUT, call_timeout=settings.CALL_TIMEOUT,
        mutation_timeout=settings.MUTATION_TIMEOUT, cache_ttl=settings.CACHE_TTL,
        max_reconnect_tries=settings.MAX_RECONNECT_TRIES, backoff_cap=settings.BACKOFF_CAP,
    )
    client.start()
    log.info("Uptime Kuma V2 API %s starting; Kuma=%s", __version__, settings.KUMA_SERVER)
    yield
    client.stop()


app = FastAPI(title="Uptime Kuma V2 API", version=__version__, lifespan=lifespan)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _ratelimit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})


@app.exception_handler(KumaError)
async def _kuma_error_handler(request: Request, exc: KumaError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


async def kcall(event, data=None, *, mutation=False):
    """Bounded entry into the (sync) Kuma call via threadpool (Codex P1-6 backpressure)."""
    try:
        await asyncio.wait_for(_sem.acquire(), timeout=settings.SEM_ACQUIRE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="server busy (too many in-flight Kuma calls)")
    try:
        return await run_in_threadpool(functools.partial(client.call, event, data, mutation=mutation))
    finally:
        _sem.release()


async def fetch_monitor(monitor_id: int):
    """Live monitor dict, or None if not found. Propagates 503/504/other errors (Codex P2-9)."""
    try:
        resp = await kcall("getMonitor", monitor_id)
    except KumaNotFound:
        return None
    return resp.get("monitor") if isinstance(resp, dict) else None


# ---------------- health (no auth) ----------------
@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/ready")
async def ready():
    h = client.health()
    ok = h["authenticated"] and h["socket_connected"] and h["cache_loaded"]
    return JSONResponse(status_code=200 if ok else 503, content=h)


# ---------------- reads ----------------
@app.get("/v1/monitors", dependencies=[Depends(require_api_key)], response_model=MonitorListOut)
@limiter.limit(settings.RATE_LIMIT)
async def list_monitors(request: Request, stale_ok: bool = False):
    h = client.health()
    healthy = h["authenticated"] and h["socket_connected"] and h["cache_loaded"]
    if not healthy and not stale_ok:
        return JSONResponse(status_code=503,
                            content={"detail": "monitor cache not ready (use ?stale_ok=true to allow stale)"})
    monitors, ts = client.cached_monitors()
    age = int(time.time() - ts) if ts else None
    out = [MonitorOut.from_kuma(m).model_dump() for m in monitors]
    return JSONResponse(content={"monitors": out, "count": len(out)},
                        headers={"X-Kuma-Cache-Age": str(age) if age is not None else "n/a"})


@app.get("/v1/monitors/{monitor_id}", dependencies=[Depends(require_api_key)], response_model=MonitorOut)
@limiter.limit(settings.RATE_LIMIT)
async def get_monitor(request: Request, monitor_id: int):
    # serve cache only when fresh (Codex P1-4); otherwise verify live
    if client.cache_fresh():
        cached, _ = client.cached_monitor(monitor_id)
        if cached:
            return MonitorOut.from_kuma(cached).model_dump()
    mon = await fetch_monitor(monitor_id)
    if not mon:
        return JSONResponse(status_code=404, content={"detail": "monitor not found"})
    return MonitorOut.from_kuma(mon).model_dump()


@app.get("/v1/monitors/{monitor_id}/beats", dependencies=[Depends(require_api_key)], response_model=BeatsOut)
@limiter.limit(settings.RATE_LIMIT)
async def get_beats(request: Request, monitor_id: int, hours: int = Query(24, ge=1, le=720)):
    resp = await kcall("getMonitorBeats", (monitor_id, hours))
    data = resp.get("data") if isinstance(resp, dict) else resp
    return {"monitor_id": monitor_id, "hours": hours, "beats": data or []}


# ---------------- mutations ----------------
@app.post("/v1/monitors", dependencies=[Depends(require_api_key)], response_model=CreateResult)
@limiter.limit(settings.RATE_LIMIT)
async def create_monitor(request: Request, body: MonitorCreate):
    try:
        mtype = body.validate_type()
    except ValueError as e:
        return JSONResponse(status_code=422, content={"detail": str(e)})
    fields = {k: v for k, v in body.model_dump().items() if k != "type"}
    payload = defaults.build_payload(mtype, fields)
    resp = await kcall("add", payload, mutation=True)
    return CreateResult(monitorID=resp.get("monitorID"), msg=resp.get("msg"))


@app.patch("/v1/monitors/{monitor_id}", dependencies=[Depends(require_api_key)], response_model=ActionResult)
@limiter.limit(settings.RATE_LIMIT)
async def patch_monitor(request: Request, monitor_id: int, body: MonitorPatch):
    current = await fetch_monitor(monitor_id)  # fetch-merge-edit
    if not current:
        return JSONResponse(status_code=404, content={"detail": "monitor not found"})
    merged = defaults.strip_server_owned(current)
    merged["id"] = monitor_id
    merged.update(body.changed())
    if merged.get("conditions") is None:
        merged["conditions"] = []
    out = await kcall("editMonitor", merged, mutation=True)
    return ActionResult(msg=out.get("msg") if isinstance(out, dict) else None)


@app.delete("/v1/monitors/{monitor_id}", dependencies=[Depends(require_api_key)], response_model=ActionResult)
@limiter.limit(settings.RATE_LIMIT)
async def delete_monitor(request: Request, monitor_id: int):
    out = await kcall("deleteMonitor", monitor_id, mutation=True)
    client.evict(monitor_id)
    return ActionResult(msg=out.get("msg") if isinstance(out, dict) else None)


@app.post("/v1/monitors/{monitor_id}/pause", dependencies=[Depends(require_api_key)], response_model=ActionResult)
@limiter.limit(settings.RATE_LIMIT)
async def pause_monitor(request: Request, monitor_id: int):
    out = await kcall("pauseMonitor", monitor_id, mutation=True)
    return ActionResult(msg=out.get("msg") if isinstance(out, dict) else None)


@app.post("/v1/monitors/{monitor_id}/resume", dependencies=[Depends(require_api_key)], response_model=ActionResult)
@limiter.limit(settings.RATE_LIMIT)
async def resume_monitor(request: Request, monitor_id: int):
    out = await kcall("resumeMonitor", monitor_id, mutation=True)
    return ActionResult(msg=out.get("msg") if isinstance(out, dict) else None)
