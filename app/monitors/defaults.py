"""Per-monitor-type default payloads for `add`/`editMonitor`.

Verified against Uptime Kuma 2.2.1. `conditions: []` is the field that breaks
older clients (NOT NULL in 2.2.x). Server-owned fields are never sent on create.
For other Kuma versions these need matrix verification (see tests/fixtures).
"""

SUPPORTED_TYPES = ("http", "ping", "tcp", "keyword", "group")

# Fields Kuma owns/sets itself; strip before `add`.
SERVER_OWNED = {
    "id", "createdDate", "updatedDate", "active", "forceInactive",
    "maintenance", "status", "uptime", "avgPing", "lastBeat",
    "certInfo", "tags", "screenshot", "includeSensitiveData",
}

_COMMON = {
    "interval": 60,
    "retryInterval": 60,
    "resendInterval": 0,
    "maxretries": 1,
    "upsideDown": False,
    "description": None,
    "notificationIDList": {},
    "conditions": [],
    "parent": None,
    # Kuma's add handler reads accepted_statuscodes for all types; provide a safe default.
    "accepted_statuscodes": ["200-299"],
}

_BY_TYPE = {
    "http": {
        "type": "http",
        "url": "https://example.com",
        "method": "GET",
        "maxredirects": 10,
        "accepted_statuscodes": ["200-299"],
        "expiryNotification": False,
        "ignoreTls": False,
        "headers": None,
        "body": None,
        "authMethod": None,
        "httpBodyEncoding": "json",
        "dns_resolve_type": "A",
        "dns_resolve_server": "1.1.1.1",
    },
    "ping": {
        "type": "ping",
        "hostname": "127.0.0.1",
        "packetSize": 56,
    },
    "tcp": {
        "type": "tcp",
        "hostname": "127.0.0.1",
        "port": 80,
    },
    "keyword": {
        "type": "keyword",
        "url": "https://example.com",
        "keyword": "ok",
        "method": "GET",
        "maxredirects": 10,
        "accepted_statuscodes": ["200-299"],
        "ignoreTls": False,
        "headers": None,
        "body": None,
        "authMethod": None,
        "httpBodyEncoding": "json",
    },
    # A "group" is a container monitor (no probe of its own); children reference it via `parent`.
    "group": {
        "type": "group",
    },
}


def build_payload(monitor_type: str, fields: dict) -> dict:
    """Merge user fields onto a complete default object for the given type."""
    if monitor_type not in _BY_TYPE:
        raise ValueError(f"unsupported monitor type '{monitor_type}'")
    payload = {"name": ""}
    payload.update(_COMMON)
    payload.update(_BY_TYPE[monitor_type])
    for k, v in fields.items():
        if v is not None:
            payload[k] = v
    payload["type"] = monitor_type
    return payload


def strip_server_owned(monitor: dict) -> dict:
    return {k: v for k, v in monitor.items() if k not in SERVER_OWNED}
