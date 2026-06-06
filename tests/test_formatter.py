from datetime import UTC, datetime

from src.config import load_config
from src.utils.formatter import SCHEMA_VERSION, format_payload


def test_formatter_preserves_partial_readings_and_errors() -> None:
    settings = load_config({"MQTT_HOST": "core.local", "DEVICE_ID": "edge-01"})
    payload = format_payload(
        settings,
        {
            "pod_1": {
                "soil_moisture": {"soil_moisture_percent": 45.2},
                "air": {"error": {"sensor": "bme280", "message": "timeout"}},
            },
            "pod_2": {},
            "shared": {"light": {"light_lux": 12000.0}},
        },
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["timestamp_utc"] == "2026-06-06T10:00:00Z"
    assert payload["pods"]["pod_1"]["metrics"]["soil_moisture_percent"] == 45.2
    assert payload["pods"]["pod_1"]["metrics"]["light_lux"] == 12000.0
    assert payload["pods"]["pod_1"]["errors"] == [{"sensor": "bme280", "message": "timeout"}]
    assert payload["pods"]["pod_2"]["metrics"]["light_lux"] == 12000.0
