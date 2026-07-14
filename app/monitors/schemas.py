"""Stable public request/response schemas (do not pass raw Kuma objects)."""
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field

from .defaults import SUPPORTED_TYPES

StatusCode = Annotated[str, Field(max_length=20)]


class MonitorCreate(BaseModel):
    type: str = Field("http", max_length=20, description="Monitor type")
    name: str = Field(min_length=1, max_length=150)
    # http / keyword
    url: Optional[str] = Field(None, max_length=2000)
    method: Optional[str] = Field(None, max_length=10)
    keyword: Optional[str] = Field(None, max_length=500)
    accepted_statuscodes: Optional[list[StatusCode]] = Field(None, max_length=50)
    maxredirects: Optional[int] = Field(None, ge=0, le=100)
    ignoreTls: Optional[bool] = None
    headers: Optional[str] = Field(None, max_length=10000)
    body: Optional[str] = Field(None, max_length=100000)
    # ping / tcp
    hostname: Optional[str] = Field(None, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    # common
    interval: Optional[int] = Field(None, ge=20, le=86400)
    retryInterval: Optional[int] = Field(None, ge=0, le=86400)
    resendInterval: Optional[int] = Field(None, ge=0, le=100000)
    maxretries: Optional[int] = Field(None, ge=0, le=100)
    upsideDown: Optional[bool] = None
    description: Optional[str] = Field(None, max_length=2000)
    # id of a "group"-type monitor to nest this monitor under (monitor group)
    parent: Optional[int] = Field(None, ge=1)

    def validate_type(self) -> str:
        if self.type not in SUPPORTED_TYPES:
            raise ValueError(
                f"unsupported monitor type '{self.type}'. v1 supports: {', '.join(SUPPORTED_TYPES)}"
            )
        return self.type


class MonitorPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=150)
    url: Optional[str] = Field(None, max_length=2000)
    method: Optional[str] = Field(None, max_length=10)
    keyword: Optional[str] = Field(None, max_length=500)
    hostname: Optional[str] = Field(None, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    interval: Optional[int] = Field(None, ge=20, le=86400)
    retryInterval: Optional[int] = Field(None, ge=0, le=86400)
    resendInterval: Optional[int] = Field(None, ge=0, le=100000)
    maxretries: Optional[int] = Field(None, ge=0, le=100)
    upsideDown: Optional[bool] = None
    accepted_statuscodes: Optional[list[StatusCode]] = Field(None, max_length=50)
    description: Optional[str] = Field(None, max_length=2000)
    # id of a "group"-type monitor to move this monitor under (monitor group)
    parent: Optional[int] = Field(None, ge=1)

    def changed(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class MonitorOut(BaseModel):
    id: int
    name: str
    type: Optional[str] = None
    url: Optional[str] = None
    hostname: Optional[str] = None
    port: Optional[int] = None
    interval: Optional[int] = None
    active: Optional[bool] = None
    maxretries: Optional[int] = None
    method: Optional[str] = None
    keyword: Optional[str] = None
    description: Optional[str] = None
    retryInterval: Optional[int] = None
    resendInterval: Optional[int] = None
    maxredirects: Optional[int] = None
    accepted_statuscodes: Optional[list[Any]] = None
    expiryNotification: Optional[bool] = None
    ignoreTls: Optional[bool] = None
    upsideDown: Optional[bool] = None
    tags: Optional[list[Any]] = None
    parent: Optional[int] = None

    @classmethod
    def from_kuma(cls, m: dict) -> "MonitorOut":
        return cls(
            id=m.get("id"),
            name=m.get("name", ""),
            type=m.get("type"),
            url=m.get("url"),
            hostname=m.get("hostname"),
            port=m.get("port"),
            interval=m.get("interval"),
            active=m.get("active"),
            maxretries=m.get("maxretries"),
            method=m.get("method"),
            keyword=m.get("keyword"),
            description=m.get("description"),
            retryInterval=m.get("retryInterval"),
            resendInterval=m.get("resendInterval"),
            maxredirects=m.get("maxredirects"),
            accepted_statuscodes=m.get("accepted_statuscodes"),
            expiryNotification=m.get("expiryNotification"),
            ignoreTls=m.get("ignoreTls"),
            upsideDown=m.get("upsideDown"),
            tags=m.get("tags"),
            parent=m.get("parent"),
        )


class CreateResult(BaseModel):
    ok: bool = True
    monitorID: int
    msg: Optional[str] = None


class ActionResult(BaseModel):
    ok: bool = True
    msg: Optional[str] = None


class Beat(BaseModel):
    status: Optional[int] = None
    time: Optional[str] = None
    ping: Optional[float] = None
    msg: Optional[str] = None
    important: Optional[bool] = None


class TagOut(BaseModel):
    id: int
    name: str
    color: Optional[str] = None


class TagsOut(BaseModel):
    tags: list[TagOut]


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: Optional[str] = Field("#4caf50", max_length=20)


class MonitorTagIn(BaseModel):
    """Attach a tag to a monitor: by tag_id, or by name (find-or-create)."""
    tag_id: Optional[int] = None
    name: Optional[str] = Field(None, max_length=100)
    color: Optional[str] = Field("#4caf50", max_length=20)
    value: Optional[str] = Field("", max_length=255)


class MonitorListOut(BaseModel):
    monitors: list[MonitorOut]
    count: int


class BeatsOut(BaseModel):
    monitor_id: int
    hours: int
    beats: list[dict[str, Any]]


class HealthOut(BaseModel):
    status: str
    detail: Any = None


class StatusPageMonitorsIn(BaseModel):
    """Add monitors to a status page, into a named public group (created if missing)."""
    monitor_ids: list[int] = Field(min_length=1, max_length=500)
    group: str = Field("Dienste", min_length=1, max_length=150)


class StatusPageMonitorsResult(BaseModel):
    ok: bool = True
    slug: str
    group: str
    added: list[int]
    skipped: list[int]


class StatusPageCreate(BaseModel):
    """Create (or update) a status page. Idempotent: an existing slug is updated, not duplicated."""
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9._-]+$")
    title: str = Field(min_length=1, max_length=150)
    published: bool = True


class StatusPageCreateResult(BaseModel):
    ok: bool = True
    slug: str
    title: str
    published: bool
    created: bool  # False when the slug already existed and was updated in place


class NotificationCreate(BaseModel):
    """Create (or update) a notification provider. `config` carries provider-specific fields.

    Pass `id` to update an existing notification in place (keeps monitor assignments).
    Example (Rocket.Chat — note the field name is `rocketwebhookURL`):
        { "name": "RC #support", "type": "rocket.chat",
          "config": { "rocketwebhookURL": "https://chat.example/hooks/…" } }
    """
    id: Optional[int] = Field(None, ge=1)
    name: str = Field(min_length=1, max_length=150)
    type: str = Field(min_length=1, max_length=50)
    config: dict = Field(default_factory=dict)
    isDefault: bool = False
    applyExisting: bool = False


class NotificationCreateResult(BaseModel):
    ok: bool = True
    id: Optional[int] = None
    msg: Optional[str] = None


class MonitorNotificationIn(BaseModel):
    """Attach (or detach) a notification to a single monitor."""
    notification_id: int = Field(ge=1)
    enabled: bool = True


class TimeObj(BaseModel):
    hours: int = Field(ge=0, le=23)
    minutes: int = Field(ge=0, le=59)


class MaintenanceCreate(BaseModel):
    """Create a maintenance window (suppresses notifications for assigned monitors while active).

    Default is a daily recurring window; `timeRange` is [start, end] and may cross midnight
    (e.g. 23:00–03:00). Assign monitors afterwards via `POST /v1/maintenances/{id}/monitors`.
    """
    title: str = Field(min_length=1, max_length=150)
    description: str = ""
    strategy: str = Field("recurring-interval", max_length=40)
    intervalDay: int = Field(1, ge=1, le=365)
    timeRange: list[TimeObj] = Field(min_length=2, max_length=2)
    weekdays: list[int] = Field(default_factory=list)
    daysOfMonth: list[int] = Field(default_factory=list)
    timezoneOption: str = Field("Europe/Berlin", max_length=64)
    active: bool = True
    # bounded date range; [null, null] = unbounded recurring
    dateRange: list[Optional[str]] = Field(default_factory=lambda: [None, None])
    cron: Optional[str] = Field(None, max_length=120)
    durationMinutes: Optional[int] = Field(None, ge=1, le=1440)


class MaintenanceCreateResult(BaseModel):
    ok: bool = True
    id: Optional[int] = None
    msg: Optional[str] = None


class MaintenanceMonitorsIn(BaseModel):
    """Replace the monitor set assigned to a maintenance window."""
    monitor_ids: list[int] = Field(min_length=1, max_length=2000)
