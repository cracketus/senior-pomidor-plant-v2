from src.config import load_config
from src.main import collect_readings, run


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
    assert readings["system_health"]["pod_1_hardware"]["box_climate"]["air_temp_c"] == 26.0


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
