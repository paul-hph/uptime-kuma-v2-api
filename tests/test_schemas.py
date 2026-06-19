import pytest

from app.monitors.schemas import MonitorCreate, MonitorPatch, MonitorOut


def test_validate_type_accepts_supported():
    assert MonitorCreate(type="http", name="x").validate_type() == "http"
    assert MonitorCreate(type="ping", name="x").validate_type() == "ping"


def test_validate_type_rejects_unsupported():
    with pytest.raises(ValueError):
        MonitorCreate(type="dns", name="x").validate_type()


def test_patch_changed_drops_none():
    p = MonitorPatch(name="new", interval=None)
    assert p.changed() == {"name": "new"}


def test_monitor_out_from_kuma():
    out = MonitorOut.from_kuma({"id": 5, "name": "m", "type": "http", "url": "https://a", "active": True})
    assert out.id == 5 and out.name == "m" and out.type == "http" and out.active is True
