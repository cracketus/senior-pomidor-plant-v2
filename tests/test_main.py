import json
import os

from src.config import load_config
from src.main import TELEMETRY_REPLAY_BATCH_SIZE, _replay_pending_telemetry, collect_readings, run


def test_collect_readings_skips_disabled_pod2() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "POD2_ENABLED": "false",
        }
    )

    readings = collect_readings(settings)

    assert readings["pod_1"] is not None
    assert readings["pod_2"] is None
    assert readings["system_health"]["rpi_core"]["cpu_temp_c"] == 56.4
    assert readings["system_health"]["pod_1_hardware"]["ina219"]["bus_voltage_v"] == 3.25


def test_run_includes_health_payload(monkeypatch) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
        }
    )
    saved_payloads = []
    sent_payloads = []

    monkeypatch.setattr("src.main.save_payload", lambda _settings, payload, **_kwargs: saved_payloads.append(payload))
    monkeypatch.setattr("src.main.MqttSender.publish", lambda _sender, payload: sent_payloads.append(payload) or True)

    run(settings, sleep=lambda _seconds: None)

    assert saved_payloads[0]["system_health"]["rpi_core"]["wifi_rssi_dbm"] == -68.0
    assert sent_payloads[0]["system_health"]["pod_1_hardware"]["bus_current_ma"] == 12.4


def test_run_deletes_current_payload_after_successful_live_send(monkeypatch, tmp_path) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
            "LOCAL_STORAGE_DIR": str(tmp_path),
        }
    )
    payload = {
        "schema_version": "senior-pomidor.edge.telemetry.v2",
        "device_id": settings.device_id,
        "timestamp_utc": "2026-06-06T10:00:00Z",
        "pods": {},
    }

    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})
    monkeypatch.setattr("src.main.format_payload", lambda _settings, _readings: payload)
    monkeypatch.setattr("src.main.MqttSender.publish", lambda *_args, **_kwargs: True)

    run(settings, sleep=lambda _seconds: None)

    assert list(tmp_path.glob("*.json")) == []


def test_run_keeps_current_payload_after_failed_live_send(monkeypatch, tmp_path) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
            "LOCAL_STORAGE_DIR": str(tmp_path),
        }
    )
    payload = {
        "schema_version": "senior-pomidor.edge.telemetry.v2",
        "device_id": settings.device_id,
        "timestamp_utc": "2026-06-06T10:00:00Z",
        "pods": {},
    }

    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})
    monkeypatch.setattr("src.main.format_payload", lambda _settings, _readings: payload)
    monkeypatch.setattr("src.main.MqttSender.publish", lambda *_args, **_kwargs: False)

    run(settings, sleep=lambda _seconds: None)

    saved_files = list(tmp_path.glob("*.json"))
    assert len(saved_files) == 1
    assert json.loads(saved_files[0].read_text(encoding="utf-8")) == payload


def test_replay_deletes_queued_payload_after_mqtt_success(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_STORAGE_DIR": str(tmp_path)})
    queued_file = _write_queued_payload(tmp_path, "old.json", "2026-06-06T10:00:00Z")
    mqtt_sender = FakeTelemetrySender(results=[True])
    http_sender = FakeTelemetrySender(results=[])

    delivered = _replay_pending_telemetry(settings, mqtt_sender, http_sender, logger=NullLogger())

    assert delivered == 1
    assert not queued_file.exists()
    assert mqtt_sender.payloads[0]["timestamp_utc"] == "2026-06-06T10:00:00Z"
    assert http_sender.payloads == []


def test_replay_uses_http_fallback_when_mqtt_fails(tmp_path) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "LOCAL_STORAGE_DIR": str(tmp_path),
        }
    )
    queued_file = _write_queued_payload(tmp_path, "old.json", "2026-06-06T10:00:00Z")
    mqtt_sender = FakeTelemetrySender(results=[False])
    http_sender = FakeTelemetrySender(results=[True])

    delivered = _replay_pending_telemetry(settings, mqtt_sender, http_sender, logger=NullLogger())

    assert delivered == 1
    assert not queued_file.exists()
    assert http_sender.payloads[0]["timestamp_utc"] == "2026-06-06T10:00:00Z"


def test_replay_keeps_queue_and_stops_after_first_delivery_failure(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_STORAGE_DIR": str(tmp_path)})
    failed_file = _write_queued_payload(tmp_path, "failed.json", "2026-06-06T10:00:00Z")
    later_file = _write_queued_payload(tmp_path, "later.json", "2026-06-06T10:01:00Z")
    os.utime(failed_file, (1, 1))
    os.utime(later_file, (2, 2))
    mqtt_sender = FakeTelemetrySender(results=[False, True])
    http_sender = FakeTelemetrySender(results=[])

    delivered = _replay_pending_telemetry(settings, mqtt_sender, http_sender, logger=NullLogger())

    assert delivered == 0
    assert failed_file.exists()
    assert later_file.exists()
    assert len(mqtt_sender.payloads) == 1


def test_replay_skips_corrupt_file_and_continues(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_STORAGE_DIR": str(tmp_path)})
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{invalid", encoding="utf-8")
    valid_file = _write_queued_payload(tmp_path, "valid.json", "2026-06-06T10:01:00Z")
    os.utime(corrupt_file, (1, 1))
    os.utime(valid_file, (2, 2))
    mqtt_sender = FakeTelemetrySender(results=[True])
    http_sender = FakeTelemetrySender(results=[])

    delivered = _replay_pending_telemetry(settings, mqtt_sender, http_sender, logger=NullLogger())

    assert delivered == 1
    assert corrupt_file.exists()
    assert not valid_file.exists()


def test_replay_processes_at_most_batch_size(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_STORAGE_DIR": str(tmp_path)})
    for index in range(TELEMETRY_REPLAY_BATCH_SIZE + 1):
        queued_file = _write_queued_payload(tmp_path, f"{index:02d}.json", f"2026-06-06T10:{index:02d}:00Z")
        os.utime(queued_file, (index, index))
    mqtt_sender = FakeTelemetrySender(results=[True] * (TELEMETRY_REPLAY_BATCH_SIZE + 1))
    http_sender = FakeTelemetrySender(results=[])

    delivered = _replay_pending_telemetry(settings, mqtt_sender, http_sender, logger=NullLogger())

    assert delivered == TELEMETRY_REPLAY_BATCH_SIZE
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_run_captures_camera_when_interval_is_due(monkeypatch) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "3",
            "CAMERA_ENABLED": "true",
            "CAMERA_INTERVAL_SECONDS": "2",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
        }
    )
    captures = []
    clock_values = iter([0.0, 1.0, 2.0])
    photo_sender = FakePhotoSender()

    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})
    monkeypatch.setattr("src.main.save_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.main.MqttSender.publish", lambda *_args, **_kwargs: True)

    run(
        settings,
        camera_capture=lambda *_args, **_kwargs: captures.append("capture"),
        photo_sender=photo_sender,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock_values),
    )

    assert captures == ["capture", "capture"]
    assert photo_sender.upload_calls == 2


def test_run_skips_camera_when_disabled(monkeypatch) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
            "CAMERA_ENABLED": "false",
        }
    )
    captures = []

    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})
    monkeypatch.setattr("src.main.save_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.main.MqttSender.publish", lambda *_args, **_kwargs: True)

    run(
        settings,
        camera_capture=lambda *_args, **_kwargs: captures.append("capture"),
        sleep=lambda _seconds: None,
    )

    assert captures == []


class FakePhotoSender:
    def __init__(self) -> None:
        self.upload_calls = 0

    def send_pending(self) -> int:
        self.upload_calls += 1
        return 0


class FakeTelemetrySender:
    def __init__(self, results: list[bool]) -> None:
        self.results = iter(results)
        self.payloads = []

    def publish(self, payload):
        self.payloads.append(payload)
        return next(self.results)

    def send(self, payload):
        self.payloads.append(payload)
        return next(self.results)


class NullLogger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None


def _write_queued_payload(tmp_path, name: str, timestamp: str):
    payload = {
        "schema_version": "senior-pomidor.edge.telemetry.v2",
        "device_id": "balcony-edge-01",
        "timestamp_utc": timestamp,
        "pods": {},
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
