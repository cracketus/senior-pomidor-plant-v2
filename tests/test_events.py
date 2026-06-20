from datetime import UTC, datetime

import pytest

from src.config import load_config
from src.utils.events import EVENT_SCHEMA_VERSION, MAINTENANCE_STARTED, format_lifecycle_event


def test_format_lifecycle_event_includes_required_fields() -> None:
    settings = load_config({"MQTT_HOST": "core.local", "DEVICE_ID": "edge-01"})

    event = format_lifecycle_event(
        settings,
        MAINTENANCE_STARTED,
        reason="sensor service",
        timestamp=datetime(2026, 6, 13, 10, 0, tzinfo=UTC),
        event_id="event-1",
    )

    assert event == {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": "event-1",
        "device_id": "edge-01",
        "event_type": "maintenance_started",
        "timestamp_utc": "2026-06-13T10:00:00Z",
        "source": "operator",
        "reason": "sensor service",
    }


def test_format_lifecycle_event_omits_empty_reason() -> None:
    settings = load_config({"MQTT_HOST": "core.local", "DEVICE_ID": "edge-01"})

    event = format_lifecycle_event(settings, MAINTENANCE_STARTED, reason="")

    assert "reason" not in event


def test_format_lifecycle_event_rejects_unknown_type() -> None:
    settings = load_config({"MQTT_HOST": "core.local"})

    with pytest.raises(ValueError, match="Unsupported lifecycle event type"):
        format_lifecycle_event(settings, "unknown")
