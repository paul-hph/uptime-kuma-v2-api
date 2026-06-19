# API Reference

Base URL: `http://<host>:8000` (put behind a TLS proxy in production).

- Interactive docs (auto-generated): **`/docs`** (Swagger UI), **`/redoc`**, schema at **`/openapi.json`**.
- All `/v1/...` endpoints require header **`X-API-Key: <key>`**.
- All bodies are JSON (`Content-Type: application/json`).

## Conventions

**Error response (all endpoints):**
```json
{ "detail": "human-readable message" }
```
| Status | Meaning |
|---|---|
| 401 | missing `X-API-Key` |
| 403 | invalid API key |
| 422 | invalid input (validation) or unsupported monitor type |
| 404 | monitor not found |
| 400 / 409 | Kuma rejected the operation |
| 503 | not connected/authenticated to Kuma, cache not ready, or server busy |
| 504 | Kuma call timed out (for mutations the outcome is **unknown** — see note) |
| 429 | rate limit exceeded |

---

## Health

### `GET /health` — no auth
```json
{ "status": "ok", "version": "0.1.0" }
```

### `GET /ready` — no auth
`200` when connected+authenticated+cache loaded, else `503` with the same body.
```json
{
  "state": "authenticated",
  "socket_connected": true,
  "authenticated": true,
  "cache_loaded": true,
  "generation": 1,
  "last_update": { "monitorList": 1781877891.4, "heartbeat": 0.0, "uptime": 0.0 }
}
```

---

## Monitors

### `GET /v1/monitors` — list
Returns the cached snapshot. Response header `X-Kuma-Cache-Age` = seconds since last update.
Query: `?stale_ok=true` to allow a stale cache while disconnected (default `false` → `503`).
```json
{
  "monitors": [
    { "id": 1, "name": "My site", "type": "http", "url": "https://example.com",
      "hostname": null, "port": null, "interval": 60, "active": true, "maxretries": 1 }
  ],
  "count": 1
}
```

### `GET /v1/monitors/{id}` — single
```json
{ "id": 1, "name": "My site", "type": "http", "url": "https://example.com",
  "hostname": null, "port": null, "interval": 60, "active": true, "maxretries": 1 }
```
`404` if not found.

### `GET /v1/monitors/{id}/beats?hours=24` — heartbeats
`hours`: 1–720 (default 24).
```json
{
  "monitor_id": 1,
  "hours": 24,
  "beats": [
    { "status": 1, "time": "2026-06-19 08:27:32", "ping": 134, "msg": "200 - OK", "important": false }
  ]
}
```
`status`: 0=down, 1=up, 2=pending, 3=maintenance. `beats` are Kuma heartbeat objects.

### `POST /v1/monitors` — create
Supported `type`: `http`, `ping`, `tcp`, `keyword`. Only `type` + `name` are required;
type-specific and common fields are optional (sensible defaults applied, incl. `conditions: []`).

**Request (http):**
```json
{
  "type": "http",
  "name": "My site",
  "url": "https://example.com",
  "method": "GET",
  "interval": 60,
  "retryInterval": 60,
  "resendInterval": 0,
  "maxretries": 1,
  "maxredirects": 10,
  "accepted_statuscodes": ["200-299"],
  "ignoreTls": false,
  "upsideDown": false,
  "description": "optional"
}
```
**Request (ping):** `{ "type": "ping", "name": "GW", "hostname": "1.1.1.1" }`
**Request (tcp):**  `{ "type": "tcp", "name": "DNS", "hostname": "1.1.1.1", "port": 53 }`
**Request (keyword):** `{ "type": "keyword", "name": "KW", "url": "https://a.b", "keyword": "ok" }`

**Response `200`:**
```json
{ "ok": true, "monitorID": 7, "msg": "successAdded" }
```
`422` if `type` is unsupported.

**Field bounds:** `name` 1–150 · `url` ≤2000 · `interval` 20–86400 · `port` 1–65535 ·
`maxretries` 0–100 · `maxredirects` 0–100 · `headers` ≤10000 · `body` ≤100000.

### `PATCH /v1/monitors/{id}` — edit (fetch-merge-edit)
Send only the fields you want to change (same names/bounds as create).
**Request:** `{ "name": "Renamed", "interval": 120 }`
**Response `200`:** `{ "ok": true, "msg": "Edited Successfully." }` · `404` if not found.

### `DELETE /v1/monitors/{id}`
**Response `200`:** `{ "ok": true, "msg": "Deleted Successfully." }`

### `POST /v1/monitors/{id}/pause` · `POST /v1/monitors/{id}/resume`
**Response `200`:** `{ "ok": true, "msg": "Paused Successfully." }` / `"Resumed Successfully."`

---

## Mutation timeout note (important)
A `504` on `POST`/`PATCH`/`DELETE`/pause/resume means Kuma did not acknowledge in time —
the change **may or may not** have been applied. The API does **not** auto-retry mutations
(retrying `add` could create duplicates). Reconcile by re-reading
(`GET /v1/monitors` or `/v1/monitors/{id}`) before retrying.

## Auth & limits
- `X-API-Key` is compared in constant time; multiple keys via `API_KEYS` (comma-separated).
- Rate limit (`RATE_LIMIT`, default `120/minute`) is per valid key, otherwise per client IP.
