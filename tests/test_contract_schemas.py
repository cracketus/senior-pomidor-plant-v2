import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw

from src.config import load_config
from src.utils.camera import capture_photo
from src.utils.events import MAINTENANCE_STARTED, RECOVERY_STARTED, format_lifecycle_event
from src.utils.formatter import format_payload

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
FIXTURE_DIR = ROOT / "tests" / "fixtures"
EXAMPLE_DIR = ROOT / "examples"


def test_contract_fixtures_validate_against_schemas() -> None:
    for schema_name, fixture_name in [
        ("edge-telemetry-v2.schema.json", "edge-telemetry-v2.valid.json"),
        ("edge-event-v1.schema.json", "edge-event-v1.valid.json"),
        ("edge-photo-v1.schema.json", "edge-photo-v1.valid.json"),
    ]:
        _validate(_read_json(SCHEMA_DIR / schema_name), _read_json(FIXTURE_DIR / fixture_name))


def test_public_examples_validate_against_schemas() -> None:
    for schema_name, example_name in [
        ("edge-telemetry-v2.schema.json", "edge-telemetry-v2.example.json"),
        ("edge-event-v1.schema.json", "edge-event-v1.example.json"),
        ("edge-photo-v1.schema.json", "edge-photo-v1.example.json"),
    ]:
        _validate(_read_json(SCHEMA_DIR / schema_name), _read_json(EXAMPLE_DIR / example_name))


def test_formatted_telemetry_validates_against_schema() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "DEVICE_ID": "edge-01",
        }
    )
    payload = format_payload(
        settings,
        {
            "pod_1": {"soil_moisture": {"soil_moisture_percent": 45.2}},
            "pod_2": None,
            "shared": {
                "air": {
                    "air_temperature_c": 24.0,
                    "air_humidity_percent": 58.0,
                    "air_pressure_hpa": 1008.5,
                },
                "light": {"light_lux": 12000.0},
                "leaf_temperature": {"leaf_temp_c": 24.9},
            },
            "system_health": {
                "rpi_core": {
                    "cpu_temp_c": 56.4,
                    "filesystem_read_only": False,
                    "telemetry_buffer_file_count": 7,
                },
                "pod_1_hardware": {
                    "ina219": {"bus_voltage_v": 3.25, "bus_current_ma": 12.4},
                },
            },
        },
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    _validate(_read_json(SCHEMA_DIR / "edge-telemetry-v2.schema.json"), payload)


def test_schema_accepts_process_only_application_and_rejects_contradictory_systemd() -> None:
    schema = _read_json(SCHEMA_DIR / "edge-telemetry-v2.schema.json")
    payload = _read_json(FIXTURE_DIR / "edge-telemetry-v2.valid.json")
    application = payload["system_health"].setdefault("application", {})
    application.clear()
    application.update({"service_manager": "none", "process_running": True})
    _validate(schema, payload)

    application["systemd_available"] = False
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: error.path)
    assert errors


def test_schema_accepts_fault_isolated_process_probe_failure() -> None:
    schema = _read_json(SCHEMA_DIR / "edge-telemetry-v2.schema.json")
    payload = _read_json(FIXTURE_DIR / "edge-telemetry-v2.valid.json")
    payload["system_health"]["application"] = {}
    payload["system_health"]["errors"] = [{"sensor": "application_process", "message": "process probe failed"}]

    _validate(schema, payload)


def test_lifecycle_event_validates_against_schema() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "DEVICE_ID": "edge-01",
        }
    )
    event = format_lifecycle_event(
        settings,
        MAINTENANCE_STARTED,
        reason="sensor service",
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
        event_id="event-1",
    )

    _validate(_read_json(SCHEMA_DIR / "edge-event-v1.schema.json"), event)

    recovery_event = format_lifecycle_event(
        settings,
        RECOVERY_STARTED,
        reason="stale heartbeat",
        source="host_watchdog",
        timestamp=datetime(2026, 6, 6, 10, 1, tzinfo=UTC),
        event_id="event-2",
    )
    _validate(_read_json(SCHEMA_DIR / "edge-event-v1.schema.json"), recovery_event)


def test_capture_photo_metadata_validates_against_schema(tmp_path) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "DEVICE_ID": "edge-01",
            "MOCK_SENSORS": "true",
            "CAMERA_ENABLED": "true",
            "CAMERA_STORAGE_DIR": str(tmp_path),
            "CAMERA_MIN_SHARPNESS": "1",
        }
    )

    def runner(command, _timeout):
        _write_sharp_jpeg(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    record = capture_photo(
        settings,
        command_runner=runner,
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    assert record is not None
    _validate(_read_json(SCHEMA_DIR / "edge-photo-v1.schema.json"), record.metadata)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema: dict, instance: dict) -> None:
    Draft202012Validator(schema).validate(instance)


def _write_sharp_jpeg(path: Path) -> None:
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, 96, 8):
        for x in range(0, 96, 8):
            if (x + y) // 8 % 2 == 0:
                draw.rectangle((x, y, x + 7, y + 7), fill="black")
    image.save(path, "JPEG", quality=95)
