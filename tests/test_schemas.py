import pytest
from pydantic import ValidationError

from app.monitors.schemas import (
    MonitorCreate, MonitorPatch, MonitorOut, StatusPageMonitorsIn, StatusPageCreate,
    NotificationCreate, MonitorNotificationIn,
)
from app.monitors import defaults


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


def test_create_accepts_parent_and_flows_into_payload():
    body = MonitorCreate(type="http", name="x", url="https://a.b", interval=120, parent=1283)
    assert body.parent == 1283
    fields = {k: v for k, v in body.model_dump().items() if k != "type"}
    payload = defaults.build_payload(body.validate_type(), fields)
    assert payload["parent"] == 1283
    assert payload["interval"] == 120


def test_create_without_parent_keeps_default_none():
    body = MonitorCreate(type="http", name="x", url="https://a.b")
    fields = {k: v for k, v in body.model_dump().items() if k != "type"}
    payload = defaults.build_payload(body.validate_type(), fields)
    assert payload["parent"] is None


def test_monitor_out_reads_parent():
    out = MonitorOut.from_kuma({"id": 1, "name": "m", "parent": 1283})
    assert out.parent == 1283


def test_status_page_monitors_in_defaults_and_validation():
    spec = StatusPageMonitorsIn(monitor_ids=[1, 2, 3])
    assert spec.group == "Dienste"
    assert spec.monitor_ids == [1, 2, 3]
    with pytest.raises(ValidationError):
        StatusPageMonitorsIn(monitor_ids=[])  # min_length=1


def test_patch_parent_flows_into_changed():
    p = MonitorPatch(parent=1400)
    assert p.changed() == {"parent": 1400}


def test_group_type_builds_container_payload():
    body = MonitorCreate(type="group", name="Care-Kunden")
    assert body.validate_type() == "group"
    fields = {k: v for k, v in body.model_dump().items() if k != "type"}
    payload = defaults.build_payload("group", fields)
    assert payload["type"] == "group"
    assert payload["name"] == "Care-Kunden"
    assert payload["conditions"] == []  # the field that breaks older clients if missing


def test_status_page_create_defaults_and_slug_validation():
    sp = StatusPageCreate(slug="care", title="Helden Care")
    assert sp.published is True
    with pytest.raises(ValidationError):
        StatusPageCreate(slug="Not Valid Slug!", title="x")  # pattern rejects spaces/caps/!


def test_notification_create_defaults_and_config_passthrough():
    n = NotificationCreate(name="RC", type="rocket.chat",
                           config={"rocketchatwebhookURL": "https://c/hooks/a/b"})
    assert n.isDefault is False and n.applyExisting is False
    assert n.config["rocketchatwebhookURL"] == "https://c/hooks/a/b"


def test_monitor_notification_in_requires_positive_id():
    mn = MonitorNotificationIn(notification_id=3)
    assert mn.enabled is True
    with pytest.raises(ValidationError):
        MonitorNotificationIn(notification_id=0)  # ge=1
