import pytest

from app.monitors import defaults


def test_http_payload_has_conditions_and_overrides():
    p = defaults.build_payload("http", {"name": "x", "url": "https://a.b", "interval": 30})
    assert p["type"] == "http"
    assert p["name"] == "x"
    assert p["url"] == "https://a.b"
    assert p["interval"] == 30
    assert p["conditions"] == []  # the field that breaks older clients
    assert p["accepted_statuscodes"] == ["200-299"]


def test_ping_and_tcp_have_accepted_statuscodes():
    for t in ("ping", "tcp", "keyword"):
        p = defaults.build_payload(t, {"name": "x"})
        assert "accepted_statuscodes" in p
        assert p["conditions"] == []
        assert p["type"] == t


def test_none_values_do_not_override_defaults():
    p = defaults.build_payload("http", {"name": "x", "interval": None})
    assert p["interval"] == 60  # default kept, None ignored


def test_unsupported_type_raises():
    with pytest.raises(ValueError):
        defaults.build_payload("dns", {"name": "x"})


def test_strip_server_owned():
    m = {"id": 1, "name": "x", "active": True, "url": "https://a", "certInfo": {}, "uptime": 99}
    out = defaults.strip_server_owned(m)
    assert "id" not in out and "active" not in out and "certInfo" not in out and "uptime" not in out
    assert out["name"] == "x" and out["url"] == "https://a"
