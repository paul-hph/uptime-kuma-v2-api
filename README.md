# Uptime Kuma V2 API

[![CI](https://github.com/paul-hph/uptime-kuma-v2-api/actions/workflows/ci.yml/badge.svg)](https://github.com/paul-hph/uptime-kuma-v2-api/actions/workflows/ci.yml)
[![Build & publish](https://github.com/paul-hph/uptime-kuma-v2-api/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/paul-hph/uptime-kuma-v2-api/actions/workflows/docker-publish.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A small, self-hostable **REST API for Uptime Kuma 2.x**.

**Prebuilt image:** `ghcr.io/paul-hph/uptime-kuma-v2-api:latest` (linux/amd64 + arm64).
Using **Coolify**? See **[COOLIFY.md](COOLIFY.md)** — add the API to an existing Kuma
stack with one compose block and three env vars.

Uptime Kuma has no official REST API — only an internal Socket.IO interface. The
popular `uptime_kuma_api` Python library targets Kuma 1.21–1.23 and breaks on
2.2.x (creating monitors fails on the new `conditions` column). This project is a
thin, robust FastAPI wrapper built specifically for **Kuma 2.x**.

> ⚠️ Uses Kuma's **internal** Socket.IO API, which is not officially supported for
> third-party use. Pinned/tested against **Uptime Kuma 2.2.1**.

## Features
- Full monitor CRUD: list, get, create, edit, delete, pause, resume, heartbeats.
- One **persistent authenticated** Socket.IO connection (fast, no per-request login).
- API-key auth (constant-time, multiple keys), rate limiting, stable JSON schemas.
- Solves the `conditions` create bug via complete per-type payloads.
- Designed to stay responsive under load (serialized Kuma calls + backpressure → 503/504, never hangs).

## Quick start (against an existing Kuma)
Uses the prebuilt image — nothing to build:
```bash
cp .env.example .env   # set KUMA_USERNAME, KUMA_PASSWORD (non-2FA user), API_KEYS
docker compose up -d
curl -H "X-API-Key: <your key>" http://localhost:8000/v1/monitors
```
Or run the full stack (Kuma + API): `docker compose -f docker-compose.full.yml up -d`.

Run a one-off without compose:
```bash
docker run -d -p 127.0.0.1:8000:8000 \
  -e KUMA_SERVER=http://your-kuma:3001 \
  -e KUMA_USERNAME=admin -e KUMA_PASSWORD=secret \
  -e API_KEYS="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  ghcr.io/paul-hph/uptime-kuma-v2-api:latest
```

## Endpoints (`/v1`)
| Method | Path | Description |
|---|---|---|
| GET | `/health` | process alive (no auth) |
| GET | `/ready` | connected + authenticated to Kuma |
| GET | `/v1/monitors` | list (cached snapshot, `X-Kuma-Cache-Age` header) |
| GET | `/v1/monitors/{id}` | single monitor |
| GET | `/v1/monitors/{id}/beats?hours=24` | heartbeats |
| POST | `/v1/monitors` | create (`http`, `ping`, `tcp`, `keyword`) |
| PATCH | `/v1/monitors/{id}` | edit (fetch-merge-edit) |
| DELETE | `/v1/monitors/{id}` | delete |
| POST | `/v1/monitors/{id}/pause` · `/resume` | pause/resume |

All `/v1` endpoints require the `X-API-Key` header.

```bash
curl -H "X-API-Key: $KEY" -X POST http://localhost:8000/v1/monitors \
  -H "Content-Type: application/json" \
  -d '{"type":"http","name":"Example","url":"https://example.com","interval":60}'
```

## Configuration
See `.env.example`. Key vars: `KUMA_SERVER`, `KUMA_USERNAME`, `KUMA_PASSWORD`,
`API_KEYS` (comma-separated), `RATE_LIMIT`, `FORWARDED_ALLOW_IPS`.

## Production notes
- **TLS:** this app does not terminate TLS. Run it behind Caddy/nginx/Traefik/Cloudflare
  and set `FORWARDED_ALLOW_IPS` to your proxy IP(s).
- **Auth:** keys grant monitor mutation rights — use strong random keys, rotate them.
- **2FA:** v1 requires a Kuma user **without** 2FA. `/ready` reports an error otherwise.
- **Version:** tested against Kuma 2.2.1. Other 2.x versions need re-verification
  (monitor payload/ack shapes can change between versions).

## Error semantics
`422` invalid input · `404` not found · `400/409` Kuma rejected · `503` not
connected/authenticated or busy · `504` Kuma call timed out · `429` rate limited.
Mutations are **not** auto-retried (a timed-out `add`/`delete` may have succeeded);
on `504`, re-`GET` to reconcile.

## Development / tests
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
# unit tests (no Kuma needed):
.venv/bin/pip install pytest && .venv/bin/pytest -q
# integration: start a local Kuma 2.2.1, bootstrap, then:
.venv/bin/python scripts/smoke_test.py
.venv/bin/python scripts/loadtest.py
```

## License
MIT
