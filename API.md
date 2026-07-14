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

### `GET /v1/beats?ids=1,2,3&hours=24&limit=50` — heartbeats for many monitors (bulk)
One round-trip for a whole page of monitors. `ids`: comma-separated (max 200). `limit`: keep only the
last N beats per monitor (0 = all). Beats are fetched server-side (local to Kuma), so this is far cheaper
than one remote request per monitor.
```json
{
  "hours": 24,
  "beats": {
    "1": [ { "status": 1, "time": "…", "ping": 134, "msg": "200 - OK" } ],
    "2": [ … ],
    "7": null
  }
}
```
A monitor maps to `null` if its beats could not be fetched.

### `POST /v1/monitors` — create
Supported `type`: `http`, `ping`, `tcp`, `keyword`, `group`. Only `type` + `name` are required;
type-specific and common fields are optional (sensible defaults applied, incl. `conditions: []`).
A `group` is a container monitor with no probe of its own — create one (`{ "type": "group",
"name": "Care-Kunden" }`), then nest monitors under it by passing its id as `parent`.

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
  "description": "optional",
  "parent": 1283
}
```
`parent` (optional): id of a `group`-type monitor to nest this monitor under (monitor group).
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
Pass `parent` to move a monitor into a monitor group: `{ "parent": 1400 }`.
**Response `200`:** `{ "ok": true, "msg": "Edited Successfully." }` · `404` if not found.

### `DELETE /v1/monitors/{id}`
**Response `200`:** `{ "ok": true, "msg": "Deleted Successfully." }`

### `POST /v1/monitors/{id}/pause` · `POST /v1/monitors/{id}/resume`
**Response `200`:** `{ "ok": true, "msg": "Paused Successfully." }` / `"Resumed Successfully."`

---

## Tags

### `GET /v1/tags` — list tags
```json
{ "tags": [ { "id": 1, "name": "HostingKunden", "color": "#4caf50" } ] }
```

### `POST /v1/tags` — create a tag
**Request:** `{ "name": "HostingKunden", "color": "#4caf50" }` → **Response:** the created `TagOut`.

### `POST /v1/monitors/{id}/tags` — attach a tag to a monitor
By id **or** by name (find-or-create):
```json
{ "tag_id": 1 }
// or
{ "name": "HostingKunden", "color": "#4caf50", "value": "" }
```
**Response `200`:** `{ "ok": true, "msg": "tag added" }`.
`GET /v1/monitors/{id}` then includes a `tags` array.

### `DELETE /v1/monitors/{id}/tags/{tag_id}?value=` — detach a tag
**Response `200`:** `{ "ok": true, "msg": "tag removed" }`.

---

## Notifications

### `POST /v1/notifications` — create a notification provider
Provider-specific fields go in `config` and are merged flat into the object Kuma stores.
Set `applyExisting: true` to attach it to every existing monitor at once; otherwise assign it
per monitor (below).
```json
{
  "name": "RC #hosting-info",
  "type": "rocket.chat",
  "config": { "rocketchatwebhookURL": "https://chat.example/hooks/aaa/bbb" }
}
```
**Response `200`:** `{ "ok": true, "id": 12, "msg": "Saved." }`

### `POST /v1/monitors/{id}/notifications` — attach/detach a notification to a monitor
Fetch-merge-edit: preserves any other notifications already on the monitor.
```json
{ "notification_id": 12, "enabled": true }
```
**Response `200`:** `{ "ok": true, "msg": "Edited Successfully." }` · `enabled: false` detaches.

---

## Status pages

### `POST /v1/statuspages` — create (or update) a status page
Idempotent: a new `slug` is created via Kuma's `addStatusPage`, then `title`/`published` are set;
an existing `slug` is updated in place (title/published), preserving all other config and its
public group list. `published` defaults to `true`. `slug` must match `^[a-z0-9._-]+$`.
```json
{ "slug": "care", "title": "Helden Care", "published": true }
```
**Response `200`:**
```json
{ "ok": true, "slug": "care", "title": "Helden Care", "published": true, "created": true }
```
`created` is `false` when the slug already existed and was updated.

### `GET /v1/statuspages/{slug}` — config + public group list
```json
{
  "config": { "slug": "helden", "title": "Helden Apps", "domainNameList": [], "...": "…" },
  "publicGroupList": [
    { "id": 2, "name": "Dienste", "weight": 1, "monitorList": [ { "id": 1276, "name": "…" } ] }
  ]
}
```

### `POST /v1/statuspages/{slug}/monitors` — add monitors to a public group
Fetch-merge-save: the named `group` is created if missing; existing groups/monitors are
preserved; monitors already on the page are skipped. `group` defaults to `"Dienste"`.
```json
{ "monitor_ids": [1310, 1311, 1312], "group": "Intern" }
```
**Response `200`:**
```json
{ "ok": true, "slug": "helden", "group": "Intern", "added": [1310, 1311], "skipped": [1312] }
```

---

## Mutation timeout note (important)
A `504` on `POST`/`PATCH`/`DELETE`/pause/resume means Kuma did not acknowledge in time —
the change **may or may not** have been applied. The API does **not** auto-retry mutations
(retrying `add` could create duplicates). Reconcile by re-reading
(`GET /v1/monitors` or `/v1/monitors/{id}`) before retrying.

## Auth & limits
- `X-API-Key` is compared in constant time; multiple keys via `API_KEYS` (comma-separated).
- Rate limit (`RATE_LIMIT`, default `120/minute`) is per valid key, otherwise per client IP.
