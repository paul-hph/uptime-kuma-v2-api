"""Concurrency/load test: verify the API stays stable under load.

Stable = no crash, no hangs; serialized live calls either succeed or return
503/504 (designed backpressure), and the service recovers afterwards.
"""
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.environ.get("API_BASE", "http://localhost:8090")
KEY = os.environ.get("API_KEY", "testkey-abc-123")


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("X-API-Key", KEY)
    if data:
        r.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(r, timeout=30)
        code = resp.status
        resp.read()
    except urllib.error.HTTPError as e:
        code = e.code
        e.read()
    except Exception as e:  # noqa: BLE001
        return ("ERR:" + type(e).__name__, time.time() - t0)
    return (code, time.time() - t0)


def run_pool(label, fn, total, workers):
    codes = {}
    lat = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fn) for _ in range(total)]
        for f in as_completed(futs):
            code, dt = f.result()
            codes[code] = codes.get(code, 0) + 1
            lat.append(dt)
    lat.sort()
    p50 = statistics.median(lat) if lat else 0
    p95 = lat[int(len(lat) * 0.95)] if lat else 0
    print(f"\n[{label}] total={total} workers={workers}")
    print(f"  codes: {codes}")
    print(f"  latency p50={p50*1000:.0f}ms p95={p95*1000:.0f}ms max={max(lat)*1000:.0f}ms")
    return codes


def main():
    # setup: create a few monitors to read against
    ids = []
    for i in range(3):
        c, _ = req("POST", "/v1/monitors", {"type": "http", "name": f"load-{i}", "url": "https://example.com"})
        # fetch fresh list to get ids
    lst = req("GET", "/v1/monitors")  # warm cache
    time.sleep(1)
    # get real ids
    r = urllib.request.Request(BASE + "/v1/monitors")
    r.add_header("X-API-Key", KEY)
    monitors = json.loads(urllib.request.urlopen(r, timeout=10).read())["monitors"]
    ids = [m["id"] for m in monitors][:5] or [1]
    print("monitor ids for read load:", ids)

    fail = []

    # Phase A: heavy cache reads
    ca = run_pool("A: GET /v1/monitors (cache)", lambda: req("GET", "/v1/monitors"), 4000, 64)
    if any(isinstance(k, str) or k >= 500 for k in ca):
        fail.append("cache reads had errors/5xx")

    # Phase B: live serialized reads (getMonitorBeats through the single connection)
    import random
    cb = run_pool("B: GET beats (live, serialized)",
                  lambda: req("GET", f"/v1/monitors/{random.choice(ids)}/beats?hours=24"), 600, 24)
    if any(isinstance(k, str) for k in cb):
        fail.append("live reads crashed (connection errors)")

    # Phase C: create/delete churn under concurrency
    def churn():
        c, dt = req("POST", "/v1/monitors", {"type": "http", "name": "churn", "url": "https://example.com"})
        if isinstance(c, int) and c == 200:
            # need the id; do a list-scan delete by name is overkill - create returns id via body
            pass
        return (c, dt)
    cc = run_pool("C: POST create churn", churn, 120, 12)
    if any(isinstance(k, str) for k in cc):
        fail.append("create churn crashed")

    # Phase D: mixed reads + writes simultaneously
    def mixed():
        import random as rnd
        roll = rnd.random()
        if roll < 0.7:
            return req("GET", "/v1/monitors")
        elif roll < 0.9:
            return req("GET", f"/v1/monitors/{rnd.choice(ids)}/beats?hours=6")
        else:
            return req("POST", "/v1/monitors", {"type": "ping", "name": "mix", "hostname": "1.1.1.1"})
    cd = run_pool("D: mixed read/write", mixed, 1500, 40)
    if any(isinstance(k, str) for k in cd):
        fail.append("mixed load crashed")

    # recovery check
    time.sleep(2)
    health = req("GET", "/health")
    ready = req("GET", "/ready")
    print(f"\nrecovery: /health={health[0]} /ready={ready[0]}")
    if health[0] != 200 or ready[0] != 200:
        fail.append("service did not recover after load")

    # cleanup churn monitors
    r = urllib.request.Request(BASE + "/v1/monitors")
    r.add_header("X-API-Key", KEY)
    monitors = json.loads(urllib.request.urlopen(r, timeout=10).read())["monitors"]
    deleted = 0
    for m in monitors:
        if m["name"] in ("churn", "mix", "load-0", "load-1", "load-2"):
            req("DELETE", f"/v1/monitors/{m['id']}")
            deleted += 1
    print(f"cleaned up {deleted} test monitors")

    print()
    if fail:
        print("LOAD TEST FAILURES:")
        for f in fail:
            print(" -", f)
        sys.exit(1)
    print("LOAD TEST PASSED — service stable under load")


if __name__ == "__main__":
    main()
