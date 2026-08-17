"""End-to-end Core compatibility probe for the Raspberry Pi edge client."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import ConfigError, Settings, load_config  # noqa: E402
from src.network.http_sender import HttpSender  # noqa: E402
from src.network.mqtt_sender import MqttSender  # noqa: E402
from src.network.photo_sender import HttpPhotoSender  # noqa: E402
from src.telemetry_spool import DeliveryStatus  # noqa: E402
from src.utils.camera import PHOTO_SCHEMA_VERSION, PhotoRecord  # noqa: E402
from src.utils.formatter import format_payload  # noqa: E402

TEST_JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010101006000600000ffdb004300"
    "0302020302020303030304030304050805050404050a070706"
    "080c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b1016101113"
    "141515150c0f171816141812141514ffdb0043010304040504"
    "0509050509140d0b0d14141414141414141414141414141414"
    "14141414141414141414141414141414141414141414141414"
    "141414141414141414141414141414ffc00011080001000103"
    "012200021101031101ffc40014000100000000000000000000"
    "00000000000000008ffc4001410010000000000000000000000"
    "000000000000000ffda000c03010002110311003f00b2c001ffd9"
)

JsonGetter = Callable[[str, float], Any]


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    message: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send and verify one full Senior Pomidor edge compatibility sample.")
    parser.add_argument("--env-file", default=".env", help="Environment file to load before running the check.")
    parser.add_argument("--telemetry-read-url", help="GET URL used to verify submitted telemetry.")
    parser.add_argument("--photo-read-url", help="GET URL used to verify uploaded photo metadata.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    env.update(read_env_file(Path(args.env_file)))
    try:
        settings = load_config(env, platform_name="Linux")
    except ConfigError as exc:
        result = StepResult("configuration", False, str(exc))
        print_results([result], json_output=args.json)
        return 1

    telemetry_read_url = args.telemetry_read_url or env.get("CORE_TELEMETRY_READ_URL")
    photo_read_url = args.photo_read_url or env.get("PHOTO_METADATA_READ_URL")
    results = run_compatibility_check(
        settings,
        telemetry_read_url=telemetry_read_url,
        photo_read_url=photo_read_url,
    )
    print_results(results, json_output=args.json)
    return 1 if any(not result.ok for result in results) else 0


def run_compatibility_check(
    settings: Settings,
    *,
    telemetry_read_url: str | None = None,
    photo_read_url: str | None = None,
    mqtt_sender: MqttSender | None = None,
    http_sender: HttpSender | None = None,
    photo_sender: HttpPhotoSender | None = None,
    get_json: JsonGetter | None = None,
    timestamp: datetime | None = None,
) -> list[StepResult]:
    timestamp = timestamp or datetime.now(UTC)
    payload = build_test_payload(settings, timestamp)
    get_json = get_json or requests_get_json
    mqtt_sender = mqtt_sender or MqttSender(settings)
    http_sender = http_sender or HttpSender(settings)
    results: list[StepResult] = []

    mqtt_ok = mqtt_sender.publish(payload)
    results.append(
        StepResult("mqtt_telemetry", mqtt_ok, "MQTT telemetry published." if mqtt_ok else "MQTT publish failed.")
    )

    http_result = http_sender.send(payload)
    http_ok = http_result.status in {DeliveryStatus.ACCEPTED, DeliveryStatus.DUPLICATE}
    results.append(
        StepResult(
            "http_telemetry",
            http_ok,
            "HTTP telemetry accepted."
            if http_ok
            else "HTTP telemetry failed; ensure HTTP_ENABLED=true and CORE_HTTP_URL is correct.",
        )
    )

    with tempfile.TemporaryDirectory(prefix="senior-pomidor-compat-") as tmp:
        photo_record = build_test_photo(settings, Path(tmp), timestamp)
        photo_sender = photo_sender or HttpPhotoSender(settings)
        photo_ok = photo_sender.send(photo_record)
        results.append(
            StepResult(
                "photo_upload",
                photo_ok,
                "Photo uploaded."
                if photo_ok
                else "Photo upload failed; ensure PHOTO_UPLOAD_ENABLED=true and PHOTO_UPLOAD_URL is correct.",
            )
        )

        telemetry_url = telemetry_read_url or default_read_url(
            settings.core_http_url, {"device_id": settings.device_id}
        )
        results.append(
            verify_read_api(
                "telemetry_read",
                telemetry_url,
                get_json=get_json,
                timeout=settings.http_timeout_seconds,
                required_values=[settings.device_id, payload["timestamp_utc"]],
            )
        )

        photo_url = photo_read_url or default_read_url(
            settings.photo_upload_url, {"photo_id": str(photo_record.metadata["photo_id"])}
        )
        results.append(
            verify_read_api(
                "photo_metadata_read",
                photo_url,
                get_json=get_json,
                timeout=settings.http_timeout_seconds,
                required_values=[str(photo_record.metadata["photo_id"]), settings.device_id],
            )
        )

    return results


def build_test_payload(settings: Settings, timestamp: datetime) -> dict[str, Any]:
    return format_payload(
        settings,
        {
            "pod_1": {"compatibility": {"soil_moisture_percent": 42.0}},
            "pod_2": None,
            "shared": {
                "air": {"air_temperature_c": 24.0, "air_humidity_percent": 58.0},
                "light": {"light_lux": 1200.0},
            },
            "system_health": {
                "rpi_core": {"compatibility_probe": True},
                "network": {"compatibility_probe": True},
            },
        },
        timestamp=timestamp,
    )


def build_test_photo(settings: Settings, directory: Path, timestamp: datetime) -> PhotoRecord:
    stamp = timestamp.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    photo_id = f"compatibility_{stamp}_{settings.device_id}"
    image_path = directory / f"{photo_id}.jpg"
    metadata_path = directory / f"{photo_id}.json"
    image_path.write_bytes(TEST_JPEG_BYTES)
    metadata: dict[str, object] = {
        "schema_version": PHOTO_SCHEMA_VERSION,
        "photo_id": photo_id,
        "device_id": settings.device_id,
        "captured_at_utc": timestamp.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "file_name": image_path.name,
        "file_size_bytes": len(TEST_JPEG_BYTES),
        "sharpness_score": 999.0,
        "upload_status": "pending",
        "uploaded_at_utc": None,
    }
    metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return PhotoRecord(image_path=image_path, metadata_path=metadata_path, metadata=metadata)


def verify_read_api(
    name: str,
    url: str | None,
    *,
    get_json: JsonGetter,
    timeout: float,
    required_values: list[str],
) -> StepResult:
    if not url:
        return StepResult(name, False, "Read API URL is not configured.")
    try:
        body = get_json(url, timeout)
    except Exception as exc:  # noqa: BLE001 - report compatibility failure context
        return StepResult(name, False, f"Read API request failed for {url}: {exc}")
    missing = [value for value in required_values if not contains_value(body, value)]
    if missing:
        return StepResult(name, False, f"Read API response did not include expected values: {', '.join(missing)}")
    return StepResult(name, True, f"Read API verified expected values at {url}.")


def default_read_url(write_url: str | None, query: dict[str, str]) -> str | None:
    if not write_url:
        return None
    separator = "&" if "?" in write_url else "?"
    return f"{write_url}{separator}{urlencode(query)}"


def requests_get_json(url: str, timeout: float) -> Any:
    import requests

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def contains_value(body: Any, expected: str) -> bool:
    if isinstance(body, str):
        return body == expected
    if isinstance(body, dict):
        return any(contains_value(value, expected) for value in body.values())
    if isinstance(body, list):
        return any(contains_value(item, expected) for item in body)
    return False


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def print_results(results: list[StepResult], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"steps": [result.__dict__ for result in results]}, indent=2, sort_keys=True))
        return
    for result in results:
        status = "pass" if result.ok else "fail"
        print(f"[{status}] {result.name}: {result.message}")


if __name__ == "__main__":
    raise SystemExit(main())
