import json
from datetime import UTC, datetime

from scripts import compatibility_check
from src.config import load_config
from src.telemetry_spool import DeliveryResult, DeliveryStatus


def test_compatibility_check_runs_all_steps_successfully() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "mqtt.local",
            "DEVICE_ID": "edge-01",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "http://core.local/api/v1/edge/telemetry",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "http://core.local/api/v1/edge/photos",
        },
        platform_name="Linux",
    )
    mqtt = FakeMqttSender(result=True)
    http = FakeHttpSender(result=DeliveryResult(DeliveryStatus.ACCEPTED, "record-1"))
    photo = FakePhotoSender(result=True)

    results = compatibility_check.run_compatibility_check(
        settings,
        mqtt_sender=mqtt,
        http_sender=http,
        photo_sender=photo,
        get_json=lambda _url, _timeout: {
            "device_id": "edge-01",
            "timestamp_utc": "2026-06-06T10:00:00Z",
            "photo_id": "compatibility_20260606T100000Z_edge-01",
        },
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    assert [result.name for result in results] == [
        "mqtt_telemetry",
        "http_telemetry",
        "photo_upload",
        "telemetry_read",
        "photo_metadata_read",
    ]
    assert all(result.ok for result in results)
    assert mqtt.payloads[0]["schema_version"] == "senior-pomidor.edge.telemetry.v2"
    assert http.payloads[0] == mqtt.payloads[0]
    assert photo.records[0].metadata["photo_id"] == "compatibility_20260606T100000Z_edge-01"


def test_compatibility_check_reports_failed_read_verification() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "mqtt.local",
            "DEVICE_ID": "edge-01",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "http://core.local/api/v1/edge/telemetry",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "http://core.local/api/v1/edge/photos",
        },
        platform_name="Linux",
    )

    results = compatibility_check.run_compatibility_check(
        settings,
        mqtt_sender=FakeMqttSender(result=True),
        http_sender=FakeHttpSender(result=DeliveryResult(DeliveryStatus.DUPLICATE, "record-1")),
        photo_sender=FakePhotoSender(result=True),
        get_json=lambda _url, _timeout: {"device_id": "other"},
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    failures = [result for result in results if not result.ok]
    assert [result.name for result in failures] == ["telemetry_read", "photo_metadata_read"]
    assert "expected values" in failures[0].message


def test_compatibility_check_reports_retrying_http_delivery_as_failed() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "mqtt.local",
            "DEVICE_ID": "edge-01",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "http://core.local/api/v1/edge/telemetry",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "http://core.local/api/v1/edge/photos",
        },
        platform_name="Linux",
    )

    results = compatibility_check.run_compatibility_check(
        settings,
        mqtt_sender=FakeMqttSender(result=True),
        http_sender=FakeHttpSender(result=DeliveryResult(DeliveryStatus.RETRY, None, "timeout")),
        photo_sender=FakePhotoSender(result=True),
        get_json=lambda _url, _timeout: {
            "device_id": "edge-01",
            "timestamp_utc": "2026-06-06T10:00:00Z",
            "photo_id": "compatibility_20260606T100000Z_edge-01",
        },
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    http_result = next(result for result in results if result.name == "http_telemetry")
    assert http_result.ok is False
    assert "failed" in http_result.message


def test_default_read_url_adds_query_string() -> None:
    assert (
        compatibility_check.default_read_url(
            "http://core.local/api/v1/edge/telemetry",
            {"device_id": "edge-01"},
        )
        == "http://core.local/api/v1/edge/telemetry?device_id=edge-01"
    )
    assert (
        compatibility_check.default_read_url(
            "http://core.local/api/v1/edge/telemetry?limit=1",
            {"device_id": "edge-01"},
        )
        == "http://core.local/api/v1/edge/telemetry?limit=1&device_id=edge-01"
    )


def test_compatibility_prints_json(capsys) -> None:
    compatibility_check.print_results(
        [compatibility_check.StepResult("mqtt", True, "ok")],
        json_output=True,
    )

    assert json.loads(capsys.readouterr().out) == {"steps": [{"message": "ok", "name": "mqtt", "ok": True}]}


class FakeMqttSender:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.payloads = []

    def publish(self, payload):
        self.payloads.append(payload)
        return self.result


class FakeHttpSender:
    def __init__(self, result: DeliveryResult) -> None:
        self.result = result
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        return self.result


class FakePhotoSender:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.records = []

    def send(self, record):
        self.records.append(record)
        return self.result
