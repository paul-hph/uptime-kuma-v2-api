"""Uptime Kuma V2 API — FastAPI wrapper around Kuma's socket.io interface."""
import asyncio
import functools
import hashlib
import json
import logging
import time
import urllib.request
from contextlib import asynccontextmanager
from typing import Any

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
    MonitorTagIn, StatusPageMonitorsIn, StatusPageMonitorsResult, TagCreate, TagOut, TagsOut,
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


def _kuma_public_status_page(slug: str, timeout: int = 15) -> dict:
    """Fetch a status page's public payload (incl. publicGroupList) from Kuma's REST API.

    The socket `getStatusPage` event returns only `config` (no group list), so the
    current group/monitor layout has to come from Kuma's own `/api/status-page/<slug>`.
    KUMA_SERVER is a trusted, operator-configured internal URL.
    """
    url = f"{settings.KUMA_SERVER.rstrip('/')}/api/status-page/{slug}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted internal URL)
        return json.loads(r.read().decode("utf-8"))


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


@app.get("/v1/beats", dependencies=[Depends(require_api_key)])
@limiter.limit(settings.RATE_LIMIT)
async def get_beats_bulk(
    request: Request,
    ids: str = Query(..., description="Comma-separated monitor ids, e.g. 1,2,3"),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(0, ge=0, le=1000, description="Keep only the last N beats per monitor (0 = all)"),
):
    """Fetch beats for many monitors in one request (one round-trip for the client).

    Beats are fetched server-side per monitor (Kuma has no bulk API), but those
    calls are local to Kuma — far cheaper than one remote HTTP round-trip each.
    """
    id_list: list[int] = []
    for raw in ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            id_list.append(int(raw))
        except ValueError:
            continue
    id_list = id_list[:200]  # bound the work per request

    out: dict[int, Any] = {}
    for mid in id_list:
        try:
            resp = await kcall("getMonitorBeats", (mid, hours))
            data = resp.get("data") if isinstance(resp, dict) else resp
            data = data or []
            if limit and len(data) > limit:
                data = data[-limit:]
            out[mid] = data
        except Exception:
            out[mid] = None
    return {"hours": hours, "beats": out}


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


# ---------------- tags ----------------
@app.get("/v1/tags", dependencies=[Depends(require_api_key)], response_model=TagsOut)
@limiter.limit(settings.RATE_LIMIT)
async def list_tags(request: Request):
    resp = await kcall("getTags")
    return {"tags": resp.get("tags", []) if isinstance(resp, dict) else []}


@app.post("/v1/tags", dependencies=[Depends(require_api_key)], response_model=TagOut)
@limiter.limit(settings.RATE_LIMIT)
async def create_tag(request: Request, body: TagCreate):
    resp = await kcall("addTag", {"name": body.name, "color": body.color}, mutation=True)
    return resp.get("tag") if isinstance(resp, dict) else resp


async def _find_tag_by_name(name: str):
    resp = await kcall("getTags")
    for t in (resp.get("tags") or []) if isinstance(resp, dict) else []:
        if t.get("name") == name:
            return t
    return None


@app.post("/v1/monitors/{monitor_id}/tags", dependencies=[Depends(require_api_key)], response_model=ActionResult)
@limiter.limit(settings.RATE_LIMIT)
async def add_monitor_tag(request: Request, monitor_id: int, body: MonitorTagIn):
    tag_id = body.tag_id
    if not tag_id:
        if not body.name:
            return JSONResponse(status_code=422, content={"detail": "tag_id or name required"})
        tag = await _find_tag_by_name(body.name)
        if not tag:
            created = await kcall("addTag", {"name": body.name, "color": body.color}, mutation=True)
            tag = created.get("tag") if isinstance(created, dict) else None
        tag_id = tag.get("id") if tag else None
        if not tag_id:
            return JSONResponse(status_code=502, content={"detail": "could not resolve/create tag"})
    await kcall("addMonitorTag", (tag_id, monitor_id, body.value or ""), mutation=True)
    return ActionResult(msg="tag added")


@app.delete("/v1/monitors/{monitor_id}/tags/{tag_id}", dependencies=[Depends(require_api_key)], response_model=ActionResult)
@limiter.limit(settings.RATE_LIMIT)
async def remove_monitor_tag(request: Request, monitor_id: int, tag_id: int, value: str = ""):
    await kcall("deleteMonitorTag", (tag_id, monitor_id, value), mutation=True)
    return ActionResult(msg="tag removed")


# ---------------- status pages ----------------
async def _status_page_config(slug: str) -> dict | None:
    """Full editable config (Kuma's toJSON: incl. domainNameList) for a status page."""
    resp = await kcall("getStatusPage", slug)
    return resp.get("config") if isinstance(resp, dict) else None


@app.get("/v1/statuspages/{slug}", dependencies=[Depends(require_api_key)])
@limiter.limit(settings.RATE_LIMIT)
async def get_status_page(request: Request, slug: str):
    config = await _status_page_config(slug)
    if not config:
        return JSONResponse(status_code=404, content={"detail": "status page not found"})
    try:
        public = await run_in_threadpool(_kuma_public_status_page, slug)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"detail": f"could not read status page groups: {e}"})
    return {"config": config, "publicGroupList": public.get("publicGroupList", [])}


@app.post("/v1/statuspages/{slug}/monitors", dependencies=[Depends(require_api_key)],
          response_model=StatusPageMonitorsResult)
@limiter.limit(settings.RATE_LIMIT)
async def add_status_page_monitors(request: Request, slug: str, body: StatusPageMonitorsIn):
    """Add monitors to a status page's public group (group created if it doesn't exist).

    Fetch-merge-save: existing groups/monitors are preserved; monitors already on the
    page are skipped. Kuma rebuilds the whole page from the payload, so the full,
    current group list must be sent back — which is exactly what we merge into.
    """
    config = await _status_page_config(slug)
    if not config:
        return JSONResponse(status_code=404, content={"detail": "status page not found"})
    try:
        public = await run_in_threadpool(_kuma_public_status_page, slug)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"detail": f"could not read status page groups: {e}"})

    groups: list[dict[str, Any]] = list(public.get("publicGroupList") or [])
    existing_ids = {m.get("id") for g in groups for m in (g.get("monitorList") or [])}

    target = next((g for g in groups if g.get("name") == body.group), None)
    if target is None:
        target = {"name": body.group, "weight": len(groups) + 1, "monitorList": []}
        groups.append(target)
    target.setdefault("monitorList", [])

    added: list[int] = []
    skipped: list[int] = []
    for mid in body.monitor_ids:
        if mid in existing_ids:
            skipped.append(mid)
            continue
        target["monitorList"].append({"id": mid})
        existing_ids.add(mid)
        added.append(mid)

    img = config.get("icon") or ""
    await kcall("saveStatusPage", (slug, config, img, groups), mutation=True)
    return StatusPageMonitorsResult(slug=slug, group=body.group, added=added, skipped=skipped)
