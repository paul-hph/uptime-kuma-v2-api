"""Thread-safe, persistent Socket.IO client for Uptime Kuma 2.x.

Design (plan v3 + Codex review):
- ONE persistent connection. python-socketio is synchronous; FastAPI calls `call()`
  from a threadpool. A single `_io_lock` serialises ALL socket I/O (connect /
  disconnect / login / call) so the lifecycle can't interleave.
- Explicit state machine + connection generation + cache-loaded gate + separate
  timeouts + normalised errors. A pure call timeout does NOT tear down the socket;
  real socket errors do.
"""
import logging
import random
import threading
import time

import socketio

log = logging.getLogger("kuma")

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED_UNAUTH = "connected_unauth"
AUTHENTICATED = "authenticated"
RECONNECTING = "reconnecting"
DEGRADED = "degraded"


class KumaError(Exception):
    """Kuma rejected the operation (maps to HTTP 4xx)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class KumaNotFound(KumaError):
    def __init__(self, message: str = "not found"):
        super().__init__(message, status=404)


class KumaUnavailable(KumaError):
    def __init__(self, message: str = "Kuma not connected/authenticated"):
        super().__init__(message, status=503)


class KumaTimeout(KumaError):
    def __init__(self, message: str = "Kuma call timed out (outcome unknown for mutations)"):
        super().__init__(message, status=504)


# Kuma error substrings that mean "no such monitor".
_NOT_FOUND_HINTS = ("cannot read properties of null", "not found", "no such")


class KumaClient:
    def __init__(self, url, username, password, *, totp=None,
                 connect_timeout=10, login_timeout=15, lock_timeout=5,
                 call_timeout=30, mutation_timeout=60,
                 max_reconnect_tries=0, backoff_cap=30, cache_ttl=30):
        self.url = url
        self.username = username
        self.password = password
        self.totp = totp
        self.connect_timeout = connect_timeout
        self.login_timeout = login_timeout
        self.lock_timeout = lock_timeout
        self.call_timeout = call_timeout
        self.mutation_timeout = mutation_timeout
        self.max_reconnect_tries = max_reconnect_tries
        self.backoff_cap = backoff_cap
        self.cache_ttl = cache_ttl

        self.sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
        self._state = DISCONNECTED
        self._state_lock = threading.Lock()
        self._io_lock = threading.Lock()       # serialises ALL socket I/O
        self._stop = threading.Event()
        self._mgr = None
        self._token = None
        self._generation = 0
        self._cache_loaded = threading.Event()

        self._cache_lock = threading.Lock()
        self._monitors = {}
        self._ts = {"monitorList": 0.0, "heartbeat": 0.0, "uptime": 0.0}

        self._register_handlers()

    # ---- state ----
    @property
    def state(self):
        with self._state_lock:
            return self._state

    def _set_state(self, s):
        with self._state_lock:
            if self._state == s:
                return
            self._state = s
        log.info("kuma state -> %s", s)

    # ---- handlers (run on socketio bg thread) ----
    def _register_handlers(self):
        sio = self.sio

        @sio.event
        def connect():
            self._set_state(CONNECTED_UNAUTH)

        @sio.event
        def connect_error(data):
            log.warning("socket connect_error: %s", data)

        @sio.event
        def disconnect():
            self._token = None
            self._cache_loaded.clear()
            self._set_state(DISCONNECTED)

        @sio.on("monitorList")
        def on_monitor_list(data):
            with self._cache_lock:
                self._monitors = dict(data or {})
                self._ts["monitorList"] = time.time()
            self._cache_loaded.set()

        @sio.on("updateMonitorIntoList")
        def on_update_into_list(data):
            with self._cache_lock:
                if isinstance(data, dict):
                    self._monitors.update(data)
                self._ts["monitorList"] = time.time()

        @sio.on("deleteMonitorFromList")
        def on_delete_from_list(monitor_id):
            with self._cache_lock:
                self._monitors.pop(str(monitor_id), None)
                self._ts["monitorList"] = time.time()

        @sio.on("*")
        def catch_all(_event, *_args):
            return None

    # ---- lifecycle ----
    def start(self):
        self._stop.clear()
        self._mgr = threading.Thread(target=self._manage, name="kuma-manager", daemon=True)
        self._mgr.start()

    def stop(self):
        self._stop.set()
        with self._io_lock:
            try:
                self.sio.disconnect()
            except Exception:
                pass

    def _totp_now(self):
        if not self.totp:
            return ""
        import pyotp
        return pyotp.TOTP(self.totp).now()

    def _login(self):
        payload = {"username": self.username, "password": self.password, "token": self._totp_now()}
        last_err = None
        # The first call on a brand-new connection can lose its ack; split the
        # configured login_timeout across a few short attempts (bounded total).
        per_attempt = max(3, self.login_timeout // 3)
        for attempt in range(1, 4):
            try:
                resp = self.sio.call("login", payload, timeout=per_attempt)
            except socketio.exceptions.TimeoutError as e:
                last_err = e
                log.warning("login attempt %d timed out, retrying", attempt)
                time.sleep(0.5)
                continue
            if not isinstance(resp, dict):
                raise KumaError("unexpected login response", 503)
            if resp.get("tokenRequired"):
                raise KumaError("Kuma requires 2FA; not supported in v1 (use a non-2FA user)", 503)
            if not resp.get("ok"):
                raise KumaError("login failed: %s" % resp.get("msg"), 503)
            self._token = resp.get("token")
            return
        raise KumaError("login timed out after retries: %s" % last_err, 503)

    def _connect_and_login(self):
        """Holds _io_lock; performs a clean (re)connect + login."""
        with self._io_lock:
            if self.sio.connected:
                try:
                    self.sio.disconnect()
                except Exception:
                    pass
            self._cache_loaded.clear()
            self._set_state(CONNECTING)
            self.sio.connect(self.url, wait=True, wait_timeout=self.connect_timeout)
            time.sleep(0.5)  # let the connection settle; avoids first-call ack race
            self._login()
            if not self.sio.connected:
                raise KumaError("disconnected during login", 503)
            self._generation += 1
            self._set_state(AUTHENTICATED)

    def _manage(self):
        tries = 0
        while not self._stop.is_set():
            st = self.state
            if st == AUTHENTICATED:
                self._stop.wait(1.0)
                continue
            if st in (CONNECTING, CONNECTED_UNAUTH):
                self._stop.wait(0.3)
                continue
            # DISCONNECTED / RECONNECTING / DEGRADED -> (re)connect
            try:
                self._connect_and_login()
                tries = 0
                log.info("kuma authenticated as %s (gen %d)", self.username, self._generation)
            except Exception as e:  # noqa: BLE001
                tries += 1
                log.warning("connect/login failed (try %d): %s", tries, e)
                with self._io_lock:
                    try:
                        self.sio.disconnect()
                    except Exception:
                        pass
                capped = self.max_reconnect_tries and tries >= self.max_reconnect_tries
                self._set_state(DEGRADED if capped else RECONNECTING)
                delay = min(self.backoff_cap, 2 ** min(tries, 6))
                delay *= 0.8 + 0.4 * random.random()
                self._stop.wait(delay)

    # ---- calls ----
    def call(self, event, data=None, *, mutation=False):
        if self.state != AUTHENTICATED:
            raise KumaUnavailable()
        timeout = self.mutation_timeout if mutation else self.call_timeout
        if not self._io_lock.acquire(timeout=self.lock_timeout):
            raise KumaUnavailable("busy: could not acquire Kuma I/O lock in time")
        try:
            # recheck after acquiring the lock (state may have changed while we waited)
            if self.state != AUTHENTICATED or not self.sio.connected:
                raise KumaUnavailable("connection not ready")
            try:
                if data is None:
                    resp = self.sio.call(event, timeout=timeout)
                else:
                    resp = self.sio.call(event, data, timeout=timeout)
            except socketio.exceptions.TimeoutError:
                # a slow call does NOT necessarily mean a dead socket -> don't tear down
                raise KumaTimeout()
            except Exception as e:  # noqa: BLE001 (real socket error)
                self._set_state(DISCONNECTED)
                raise KumaUnavailable("socket error: %s" % e)
        finally:
            self._io_lock.release()
        return self._normalize(resp)

    @staticmethod
    def _normalize(resp):
        if isinstance(resp, dict) and resp.get("ok") is False:
            msg = resp.get("msg") or "Kuma rejected the operation"
            if any(h in str(msg).lower() for h in _NOT_FOUND_HINTS):
                raise KumaNotFound(msg)
            raise KumaError(msg, 400)
        return resp

    # ---- cache ----
    def cached_monitors(self):
        with self._cache_lock:
            return list(self._monitors.values()), self._ts["monitorList"]

    def cached_monitor(self, monitor_id):
        with self._cache_lock:
            return self._monitors.get(str(monitor_id)), self._ts["monitorList"]

    def evict(self, monitor_id):
        with self._cache_lock:
            self._monitors.pop(str(monitor_id), None)

    def cache_fresh(self):
        """True if authenticated, socket connected, cache loaded, and within TTL."""
        if self.state != AUTHENTICATED or not self.sio.connected or not self._cache_loaded.is_set():
            return False
        with self._cache_lock:
            age = time.time() - self._ts["monitorList"]
        return age <= self.cache_ttl

    @property
    def cache_loaded(self):
        return self._cache_loaded.is_set()

    def health(self):
        with self._cache_lock:
            ts = dict(self._ts)
        return {
            "state": self.state,
            "socket_connected": bool(self.sio.connected),
            "authenticated": self.state == AUTHENTICATED,
            "cache_loaded": self._cache_loaded.is_set(),
            "generation": self._generation,
            "last_update": ts,
        }
