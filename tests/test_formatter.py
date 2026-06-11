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
            "system_health": {
                "rpi_core": {"cpu_temp_c": 56.4, "wifi_rssi_dbm": -68},
                "pod_1_hardware": {
                    "ina219": {"bus_voltage_v": 3.25, "bus_current_ma": 12.4},
                },
            },
        },
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["timestamp_utc"] == "2026-06-06T10:00:00Z"
    assert payload["pods"]["pod_1"]["enabled"] is True
    assert payload["pods"]["pod_1"]["metrics"]["soil_moisture_percent"] == 45.2
    assert payload["pods"]["pod_1"]["metrics"]["light_lux"] == 12000.0
    assert payload["pods"]["pod_1"]["errors"] == [{"sensor": "bme280", "message": "timeout"}]
    assert payload["pods"]["pod_2"]["metrics"]["light_lux"] == 12000.0
    assert payload["system_health"] == {
        "rpi_core": {"cpu_temp_c": 56.4, "wifi_rssi_dbm": -68.0},
        "pod_1_hardware": {
            "bus_voltage_v": 3.25,
            "bus_current_ma": 12.4,
        },
        "errors": [],
    }


def test_formatter_marks_disabled_pod() -> None:
    settings = load_config({"MQTT_HOST": "core.local", "DEVICE_ID": "edge-01"})
    payload = format_payload(
        settings,
        {"pod_1": {}, "pod_2": None, "shared": {"light": {"light_lux": 12000.0}}},
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    assert payload["pods"]["pod_2"] == {"enabled": False, "metrics": {}, "errors": []}


def test_formatter_isolates_health_errors() -> None:
    settings = load_config({"MQTT_HOST": "core.local", "DEVICE_ID": "edge-01"})
    payload = format_payload(
        settings,
        {
            "pod_1": {},
            "pod_2": {},
            "shared": {},
            "system_health": {
                "rpi_core": {
                    "cpu_temp_c": 56.4,
                    "errors": [{"sensor": "rpi_wifi_rssi", "message": "RSSI unavailable"}],
                },
                "pod_1_hardware": {
                    "ina219": {"error": {"sensor": "ina219", "message": "i2c timeout"}},
                },
            },
        },
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    assert payload["system_health"] == {
        "rpi_core": {"cpu_temp_c": 56.4},
        "pod_1_hardware": {},
        "errors": [
            {"sensor": "rpi_wifi_rssi", "message": "RSSI unavailable"},
            {"sensor": "ina219", "message": "i2c timeout"},
        ],
    }
