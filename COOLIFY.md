# Adding the API to an existing Uptime Kuma stack in Coolify

This adds a REST API to an Uptime Kuma you already run in Coolify. You don't build
anything — Coolify pulls the prebuilt image `ghcr.io/paul-hph/uptime-kuma-v2-api`.

Two ways: **A) add a service to the existing Kuma stack (recommended)** or
**B) deploy as a standalone resource**.

---

## A) Add it to your existing Uptime Kuma stack (recommended)

The API then reaches Kuma over the internal Docker network via the Kuma service
name, and Coolify gives the API its own HTTPS domain automatically.

1. In Coolify, open your **Uptime Kuma** service → **Configuration → Docker Compose**.
2. Add this service to the `services:` block (use your real Kuma service name in
   `KUMA_SERVER` — usually `uptime-kuma`):

   ```yaml
     uptime-kuma-v2-api:
       image: ghcr.io/paul-hph/uptime-kuma-v2-api:latest
       environment:
         # Coolify generates a public HTTPS URL for port 8000 and routes it via its proxy:
         - SERVICE_FQDN_KUMAAPI_8000
         - KUMA_SERVER=http://uptime-kuma:3001
         - KUMA_USERNAME=${KUMA_USERNAME}
         - KUMA_PASSWORD=${KUMA_PASSWORD}
         - API_KEYS=${API_KEYS}
         - FORWARDED_ALLOW_IPS=*   # behind Coolify's proxy
       depends_on:
         uptime-kuma:
           condition: service_healthy
   ```

3. Go to **Environment Variables** and add:
   - `KUMA_USERNAME` = a Kuma login **without 2FA**
   - `KUMA_PASSWORD` = that user's password
   - `API_KEYS` = one or more strong random keys (comma-separated). Generate one:
     ```bash
     python3 -c "import secrets;print(secrets.token_urlsafe(32))"
     ```
4. Click **Deploy**. Coolify builds nothing, pulls the image, wires the network,
   and assigns an HTTPS domain to the API (shown on the service page).
5. Test:
   ```bash
   curl -H "X-API-Key: <your key>" https://<the-coolify-domain>/v1/monitors
   ```

That's it. TLS, networking, and restarts are handled by Coolify.

> No 2FA: v1 needs a Kuma user without two-factor auth. If 2FA is on, create a
> dedicated API user without it, or disable 2FA for the user you use here.

---

## B) Standalone resource (API in its own project)

Use this if you'd rather keep the API separate from the Kuma stack.

1. Coolify → **+ New → Resource → Docker Image**.
2. Image: `ghcr.io/paul-hph/uptime-kuma-v2-api:latest`.
3. Set environment variables:
   - `KUMA_SERVER` = a URL the API can reach your Kuma at. Easiest is Kuma's public
     URL, e.g. `https://uptime.example.com`. (For internal networking, attach both
     to the same Coolify network and use the container name + `:3001`.)
   - `KUMA_USERNAME`, `KUMA_PASSWORD` (non-2FA), `API_KEYS`, `FORWARDED_ALLOW_IPS=*`.
4. Add a **Domain** for the resource (port 8000) so Coolify gives it HTTPS.
5. Deploy and test as above.

---

## Health checks
- `GET /health` — process is alive (no key).
- `GET /ready` — connected **and** authenticated to Kuma (use this as the Coolify
  health check). Returns 503 until the first monitor snapshot is loaded.

## Updating
The image tag `:latest` follows the repo's `main`. To update, redeploy the service
in Coolify (it re-pulls). Pin a version tag (e.g. `:v0.1.0`) if you want stability.

## Replacing an old REST API (e.g. medaziz `uptimekuma_restapi`)
Remove or stop the old `kuma-api` service in the same stack, add this one as above.
Client login differs: this API authenticates clients with `X-API-Key`, not a
username/password token endpoint.
