"""Create the initial admin on a FRESH Uptime Kuma (dev/test only)."""
import sys
import socketio

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3011"
user = sys.argv[2] if len(sys.argv) > 2 else "admin"
pw = sys.argv[3] if len(sys.argv) > 3 else "admin123456"

sio = socketio.Client(reconnection=False)
sio.connect(url, transports=["websocket"], wait=True, wait_timeout=15)
try:
    # try setup (only works on a fresh install)
    try:
        resp = sio.call("setup", (user, pw), timeout=15)
        print("setup ->", resp)
    except Exception as e:  # noqa: BLE001
        print("setup failed (maybe already set up):", e)
    # verify login works
    login = sio.call("login", {"username": user, "password": pw, "token": ""}, timeout=15)
    print("login ->", {k: (v if k != "token" else "<jwt>") for k, v in (login or {}).items()})
finally:
    sio.disconnect()
