import pytest

from src.config import load_config
from src.main import collect_readings, run


def _load_config(env):
    values = {
        "HTTP_ENABLED": "true",
        "CORE_HTTP_URL": "https://core.example/telemetry",
    }
    values.update(env)
    return load_config(values)


def test_collect_readings_skips_disabled_pod2() -> None:
    settings = _load_config(
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


def test_collect_readings_reads_bme280_once_as_shared_sensor(monkeypatch) -> None:
    settings = _load_config({"MQTT_HOST": "core.local", "MOCK_SENSORS": "true"})
    calls = []

    def fake_bme280_read(**kwargs):
        calls.append(kwargs)
        return {"air_temperature_c": 24.0}

    monkeypatch.setattr("src.main.air_bme280.read", fake_bme280_read)

    readings = collect_readings(settings)

    assert readings["shared"]["air"] == {"air_temperature_c": 24.0}
    assert calls == [{"address": settings.bme280_address, "mock": True}]


def test_run_includes_health_payload(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
        }
    )
    repository = FakeRepository()
    worker = FakeWorker(repository.events)

    run(settings, repository=repository, delivery_worker=worker, sleep=lambda _seconds: None)

    stored = repository.payloads[0]
    assert stored["system_health"]["rpi_core"]["wifi_rssi_dbm"] == -68.0
    assert stored["system_health"]["application"]["process_running"] is True
    assert stored["system_health"]["pod_1_hardware"]["bus_current_ma"] == 12.4
    assert stored["system_health"]["spool"]["status"] == "OK"
    assert repository.events.index("enqueue") < repository.events.index("notify")


def test_run_captures_camera_when_interval_is_due(monkeypatch) -> None:
    settings = _load_config(
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
    repository = FakeRepository()
    worker = FakeWorker(repository.events)

    run(
        settings,
        camera_capture=lambda *_args, **_kwargs: captures.append("capture"),
        photo_sender=photo_sender,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock_values),
        repository=repository,
        delivery_worker=worker,
    )

    assert captures == ["capture", "capture"]
    assert photo_sender.upload_calls == 2


def test_run_skips_camera_when_disabled(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
            "CAMERA_ENABLED": "false",
        }
    )
    captures = []

    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})
    repository = FakeRepository()
    worker = FakeWorker(repository.events)

    run(
        settings,
        camera_capture=lambda *_args, **_kwargs: captures.append("capture"),
        sleep=lambda _seconds: None,
        repository=repository,
        delivery_worker=worker,
    )

    assert captures == []


def test_run_suspends_sampling_and_camera_on_degraded_spool_disk(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "CAMERA_ENABLED": "true",
        }
    )
    repository = DegradedDiskRepository()
    worker = FakeWorker(repository.events)
    collected = []
    captured = []

    monkeypatch.setattr("src.main.collect_readings", lambda _settings: collected.append(True))

    def stop_loop(_seconds):
        raise StopLoop

    with pytest.raises(StopLoop):
        run(
            settings,
            camera_capture=lambda *_args, **_kwargs: captured.append(True),
            sleep=stop_loop,
            repository=repository,
            delivery_worker=worker,
        )

    assert collected == []
    assert captured == []
    assert repository.payloads == []


class FakePhotoSender:
    def __init__(self) -> None:
        self.upload_calls = 0

    def send_pending(self) -> int:
        self.upload_calls += 1
        return 0


class FakeRepository:
    def __init__(self) -> None:
        self.payloads = []
        self.events = []

    def import_legacy(self, _directory):
        return 0, []

    def health(self):
        return {"status": "OK", "pending_count": 0, "in_flight_count": 0}

    def enqueue(self, payload):
        self.events.append("enqueue")
        self.payloads.append(payload)

    def cleanup_delivered(self, _days):
        return 0

    def relieve_disk_pressure(self, _days):
        return 0

    def close(self):
        self.events.append("close")


class FakeWorker:
    def __init__(self, events) -> None:
        self.events = events

    def start(self):
        self.events.append("start")

    def notify(self):
        self.events.append("notify")

    def stop(self):
        self.events.append("stop")


class DegradedDiskRepository(FakeRepository):
    def health(self):
        return {"status": "DEGRADED", "disk_status": "DEGRADED", "pending_count": 2, "in_flight_count": 0}


class StopLoop(Exception):
    pass
