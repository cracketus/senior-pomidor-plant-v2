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


def test_collect_readings_reports_each_potentially_blocking_phase() -> None:
    settings = _load_config({"MQTT_HOST": "core.local", "MOCK_SENSORS": "true"})
    phases = []

    collect_readings(settings, phases.append)

    assert "collecting:pod_1:soil_moisture" in phases
    assert "collecting:pod_1:soil_temperature" in phases
    assert "collecting:air" in phases
    assert "collecting:light" in phases
    assert "collecting:leaf_temperature" in phases
    assert "collecting:system_health:rpi_core" in phases
    assert "collecting:system_health:network" in phases
    assert "collecting:system_health:application" in phases
    assert "collecting:system_health:ina219" in phases


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
    lifecycle_worker = FakeLifecycleWorker(repository.events)

    run(
        settings,
        repository=repository,
        delivery_worker=worker,
        lifecycle_replay_worker=lifecycle_worker,
        sleep=lambda _seconds: None,
    )

    stored = repository.payloads[0]
    assert stored["system_health"]["rpi_core"]["wifi_rssi_dbm"] == -68.0
    assert stored["system_health"]["application"]["process_running"] is True
    assert stored["system_health"]["pod_1_hardware"]["bus_current_ma"] == 12.4
    assert stored["system_health"]["spool"]["status"] == "OK"
    assert stored["system_health"]["watchdog"]["state"] in {"unavailable", "starting", "healthy"}
    assert repository.events.index("start") < repository.events.index("enqueue")
    assert repository.events.index("enqueue") < repository.events.index("lifecycle_notify")
    assert repository.events.index("enqueue") < repository.events.index("notify")


def test_run_publishes_startup_heartbeat_before_spool_initialization(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
        }
    )
    repository = StartupOrderingRepository()
    events = repository.events
    worker = FakeWorker(events)
    lifecycle_worker = FakeLifecycleWorker(events)
    heartbeat = RecordingHeartbeat(events)

    def open_spool(_settings, _logger):
        events.append("open_spool")
        return repository

    monkeypatch.setattr("src.main._open_spool", open_spool)
    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})

    run(
        settings,
        delivery_worker=worker,
        lifecycle_replay_worker=lifecycle_worker,
        heartbeat_writer=heartbeat,
        sleep=lambda _seconds: None,
    )

    assert events.index("heartbeat:startup") < events.index("open_spool")
    assert events.index("heartbeat:startup") < events.index("import_legacy")
    assert events.index("heartbeat:startup") < events.index("cleanup_delivered")


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
    assert photo_sender.upload_calls == 3


def test_run_uploads_newly_captured_photo_in_same_tick(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
            "CAMERA_ENABLED": "true",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
        }
    )
    operations = []
    repository = FakeRepository()
    worker = FakeWorker(repository.events)
    photo_sender = FakePhotoSender(operations)
    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})

    run(
        settings,
        camera_capture=lambda *_args, **_kwargs: operations.append("capture"),
        photo_sender=photo_sender,
        repository=repository,
        delivery_worker=worker,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    assert operations == ["capture", "upload"]
    assert photo_sender.upload_calls == 1


def test_run_notifies_lifecycle_replay_after_each_persist(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "3",
        }
    )
    repository = FakeRepository()
    worker = FakeWorker(repository.events)
    lifecycle_events = []
    lifecycle_worker = FakeLifecycleWorker(lifecycle_events)
    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})

    run(
        settings,
        sleep=lambda _seconds: None,
        repository=repository,
        delivery_worker=worker,
        lifecycle_replay_worker=lifecycle_worker,
    )

    assert lifecycle_events == [
        "lifecycle_start",
        "lifecycle_notify",
        "lifecycle_notify",
        "lifecycle_notify",
        "lifecycle_stop",
    ]


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
    assert repository.events == ["start", "stop", "close"]


def test_run_starts_delivery_but_not_event_replay_before_fresh_persist_succeeds(monkeypatch) -> None:
    settings = _load_config({"MQTT_HOST": "core.local", "MOCK_SENSORS": "true"})
    repository = FailingRepository()
    worker = FakeWorker(repository.events)
    lifecycle_events = []
    lifecycle_worker = FakeLifecycleWorker(lifecycle_events)
    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})

    with pytest.raises(StopLoop):
        run(
            settings,
            repository=repository,
            delivery_worker=worker,
            lifecycle_replay_worker=lifecycle_worker,
            sleep=lambda _seconds: (_ for _ in ()).throw(StopLoop()),
        )

    assert repository.events == ["start", "enqueue_failed", "stop", "close"]
    assert "lifecycle_notify" not in lifecycle_events
    assert "notify" not in repository.events


def test_run_stops_worker_before_closing_repository_when_final_heartbeat_fails(monkeypatch, caplog) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "MAX_TICKS": "1",
        }
    )
    repository = FakeRepository()
    worker = FakeWorker(repository.events)
    heartbeat = FailingShutdownHeartbeat()
    monkeypatch.setattr("src.main.collect_readings", lambda _settings: {"pod_1": {}, "pod_2": {}, "shared": {}})

    run(
        settings,
        repository=repository,
        delivery_worker=worker,
        heartbeat_writer=heartbeat,
        sleep=lambda _seconds: None,
    )

    assert heartbeat.phases[-1] == "stopping"
    assert repository.events[-2:] == ["stop", "close"]
    assert "Final watchdog heartbeat write failed" in caplog.text


class FakePhotoSender:
    def __init__(self, events=None) -> None:
        self.upload_calls = 0
        self.events = events

    def send_pending(self, **_kwargs) -> int:
        self.upload_calls += 1
        if self.events is not None:
            self.events.append("upload")
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


class FakeLifecycleWorker:
    def __init__(self, events) -> None:
        self.events = events

    def start(self):
        self.events.append("lifecycle_start")

    def notify(self):
        self.events.append("lifecycle_notify")

    def stop(self):
        self.events.append("lifecycle_stop")


class FailingShutdownHeartbeat:
    def __init__(self) -> None:
        self.phases = []

    def write(self, phase, **_details):
        self.phases.append(phase)
        if phase == "stopping":
            raise OSError("simulated full disk")

    def persisted(self, _record_id=None):
        self.phases.append("persisted")


class RecordingHeartbeat:
    def __init__(self, events) -> None:
        self.events = events

    def write(self, phase, **_details):
        self.events.append(f"heartbeat:{phase}")

    def persisted(self, _record_id=None):
        self.events.append("heartbeat:persisted")


class DegradedDiskRepository(FakeRepository):
    def health(self):
        return {"status": "DEGRADED", "disk_status": "DEGRADED", "pending_count": 2, "in_flight_count": 0}


class StartupOrderingRepository(FakeRepository):
    def import_legacy(self, _directory):
        self.events.append("import_legacy")
        return 0, []

    def cleanup_delivered(self, _days):
        self.events.append("cleanup_delivered")
        return 0


class FailingRepository(FakeRepository):
    def enqueue(self, payload):
        self.events.append("enqueue_failed")
        raise OSError("simulated SQLite write failure")

    def increment_counter(self, _name):
        return 1


class StopLoop(Exception):
    pass
