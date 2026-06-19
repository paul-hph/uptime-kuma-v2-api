"""End-to-end smoke test against a running API + Kuma."""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("API_BASE", "http://localhost:8090")
KEY = os.environ.get("API_KEY", "testkey-abc-123")
fails = []


def call(method, path, body=None, key=KEY, expect=200):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if key:
        req.add_header("X-API-Key", key)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=30)
        code, payload = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, payload = e.code, e.read()
    ok = code == expect
    if not ok:
        fails.append(f"{method} {path} expected {expect} got {code}: {payload[:200]}")
    try:
        parsed = json.loads(payload) if payload else None
    except Exception:
        parsed = payload
    print(f"[{'OK ' if ok else 'XX '}] {method} {path} -> {code}")
    return parsed


print("== auth ==")
call("GET", "/v1/monitors", key=None, expect=401)
call("GET", "/v1/monitors", key="wrong", expect=403)

print("== reads ==")
call("GET", "/health", key=None, expect=200)
call("GET", "/ready", key=None, expect=200)
before = call("GET", "/v1/monitors", expect=200)

print("== create (conditions) ==")
created = call("POST", "/v1/monitors",
               {"type": "http", "name": "Smoke HTTP", "url": "https://example.com", "interval": 60},
               expect=200)
mid = created.get("monitorID") if isinstance(created, dict) else None
print("   new id:", mid)

print("== create other types ==")
call("POST", "/v1/monitors", {"type": "ping", "name": "Smoke Ping", "hostname": "1.1.1.1"}, expect=200)
call("POST", "/v1/monitors", {"type": "tcp", "name": "Smoke TCP", "hostname": "1.1.1.1", "port": 53}, expect=200)
call("POST", "/v1/monitors", {"type": "dns", "name": "bad"}, expect=422)

print("== get / patch ==")
call("GET", f"/v1/monitors/{mid}", expect=200)
call("PATCH", f"/v1/monitors/{mid}", {"name": "Smoke HTTP renamed", "interval": 120}, expect=200)
after_patch = call("GET", f"/v1/monitors/{mid}", expect=200)
if isinstance(after_patch, dict) and after_patch.get("name") != "Smoke HTTP renamed":
    fails.append(f"patch did not rename: {after_patch.get('name')}")

print("== beats / pause / resume ==")
call("GET", f"/v1/monitors/{mid}/beats?hours=24", expect=200)
call("POST", f"/v1/monitors/{mid}/pause", expect=200)
call("POST", f"/v1/monitors/{mid}/resume", expect=200)

print("== delete ==")
call("DELETE", f"/v1/monitors/{mid}", expect=200)
call("GET", f"/v1/monitors/{mid}", expect=404)

print()
if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("ALL SMOKE TESTS PASSED")
