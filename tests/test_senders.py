import json

import pytest

from src.config import ConfigError, load_config
from src.network.event_sender import MqttEventSender
from src.network.http_sender import HttpSender
from src.network.mqtt_sender import MqttSender
from src.network.photo_sender import PHOTO_REPLAY_BATCH_SIZE, HttpPhotoSender
from src.telemetry_spool import DeliveryStatus
from src.utils.camera import PhotoRecord


def _load_config(env):
    values = {
        "HTTP_ENABLED": "true",
        "CORE_HTTP_URL": "https://core.example/telemetry",
    }
    values.update(env)
    return load_config(values)


def test_mqtt_sender_returns_false_on_client_failure() -> None:
    settings = _load_config({"MQTT_HOST": "core.local"})
    sender = MqttSender(settings, client_factory=lambda: FailingMqttClient())

    assert sender.publish({"hello": "world"}) is False


def test_mqtt_event_sender_publishes_to_events_topic() -> None:
    settings = _load_config({"MQTT_HOST": "core.local", "DEVICE_ID": "edge-01", "MQTT_TOPIC_PREFIX": "plants"})
    client = CapturingMqttClient()
    sender = MqttEventSender(settings, client_factory=lambda: client)

    assert sender.publish({"event_type": "maintenance_started"}) is True

    assert client.published[0]["topic"] == "plants/edge-01/events"
    assert client.published[0]["qos"] == 1
    assert json.loads(client.published[0]["payload"]) == {"event_type": "maintenance_started"}


def test_mqtt_event_sender_returns_false_on_client_failure() -> None:
    settings = _load_config({"MQTT_HOST": "core.local"})
    sender = MqttEventSender(settings, client_factory=lambda: FailingMqttClient())

    assert sender.publish({"event_type": "maintenance_started"}) is False


def test_http_sender_cannot_be_disabled() -> None:
    with pytest.raises(ConfigError, match="HTTP_ENABLED=true"):
        _load_config({"MQTT_HOST": "core.local", "HTTP_ENABLED": "false"})


def test_http_sender_accepts_202_and_posts_payload() -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/api/v1/edge/telemetry",
            "HTTP_TIMEOUT_SECONDS": "7",
        }
    )
    captured = {}

    def post_func(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return Response(202, {"record_id": "record-1", "status": "accepted"})

    payload = {"schema_version": "senior-pomidor.edge.telemetry.v2", "record_id": "record-1"}

    assert HttpSender(settings, post_func=post_func).send(payload).status is DeliveryStatus.ACCEPTED
    assert captured == {
        "url": "https://core.example/api/v1/edge/telemetry",
        "json": payload,
        "timeout": 7.0,
    }


def test_http_sender_retries_invalid_ack() -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/api/v1/edge/telemetry",
        }
    )

    result = HttpSender(settings, post_func=lambda *_args, **_kwargs: Response(200)).send({"record_id": "record-1"})

    assert result.status is DeliveryStatus.RETRY


def test_http_photo_sender_uploads_multipart_and_marks_uploaded(tmp_path) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "DEVICE_ID": "edge-01",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
            "PHOTO_UPLOAD_TOKEN": "secret",
            "CAMERA_STORAGE_DIR": str(tmp_path),
            "HTTP_TIMEOUT_SECONDS": "7",
        }
    )
    record = _photo_record(tmp_path)
    captured = {}

    def post_func(url, files, data, headers, timeout):
        captured["url"] = url
        captured["file_name"] = files["photo"][0]
        captured["content_type"] = files["photo"][2]
        captured["photo_bytes"] = files["photo"][1].read()
        captured["data"] = data
        captured["headers"] = headers
        captured["timeout"] = timeout
        return Response(202)

    assert HttpPhotoSender(settings, post_func=post_func).send(record) is True

    assert captured == {
        "url": "https://core.example/photos",
        "file_name": "photo.jpg",
        "content_type": "image/jpeg",
        "photo_bytes": b"jpeg-bytes",
        "data": {
            "photo_id": "photo-1",
            "device_id": "edge-01",
            "captured_at_utc": "2026-06-06T10:00:00Z",
            "schema_version": "senior-pomidor.edge.photo.v1",
            "sharpness_score": "12.5",
        },
        "headers": {"Authorization": "Bearer secret"},
        "timeout": 7.0,
    }
    metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    assert metadata["upload_status"] == "uploaded"
    assert metadata["uploaded_at_utc"] is not None


def test_http_photo_sender_preserves_pending_on_failure(tmp_path) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
            "CAMERA_STORAGE_DIR": str(tmp_path),
        }
    )
    record = _photo_record(tmp_path)

    assert HttpPhotoSender(settings, post_func=lambda *_args, **_kwargs: Response(500)).send(record) is False

    metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    assert metadata["upload_status"] == "pending"
    assert metadata["uploaded_at_utc"] is None


def test_http_photo_sender_treats_duplicate_200_as_success(tmp_path) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
            "CAMERA_STORAGE_DIR": str(tmp_path),
        }
    )
    record = _photo_record(tmp_path)

    assert HttpPhotoSender(settings, post_func=lambda *_args, **_kwargs: Response(200)).send(record) is True


def test_http_photo_sender_disabled_returns_zero(tmp_path) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "PHOTO_UPLOAD_ENABLED": "false",
            "CAMERA_STORAGE_DIR": str(tmp_path),
        }
    )

    assert HttpPhotoSender(settings).send_pending() == 0


def test_http_photo_sender_bounds_replay_and_reports_progress(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
        }
    )
    records = [PhotoRecord(None, None, {"photo_id": f"photo-{index}"}) for index in range(20)]
    sender = HttpPhotoSender(settings)
    sent = []
    progress = []
    monkeypatch.setattr("src.network.photo_sender.list_pending_photos", lambda _settings: records)
    monkeypatch.setattr(sender, "send", lambda record: sent.append(record) or True)

    uploaded = sender.send_pending(progress=progress.append)

    assert uploaded == PHOTO_REPLAY_BATCH_SIZE
    assert sent == records[:PHOTO_REPLAY_BATCH_SIZE]
    assert progress == [f"uploading_photo:photo-{index}" for index in range(PHOTO_REPLAY_BATCH_SIZE)]


def test_http_photo_sender_advances_past_failed_batch(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
        }
    )
    records = [PhotoRecord(None, None, {"photo_id": f"photo-{index}"}) for index in range(20)]
    sender = HttpPhotoSender(settings)
    sent = []
    monkeypatch.setattr("src.network.photo_sender.list_pending_photos", lambda _settings: records)
    monkeypatch.setattr(sender, "send", lambda record: sent.append(record) and False)

    sender.send_pending()
    sender.send_pending()

    assert sent == records


def test_http_photo_sender_retries_failures_while_new_photos_arrive(monkeypatch) -> None:
    settings = _load_config(
        {
            "MQTT_HOST": "core.local",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
        }
    )
    records = [PhotoRecord(None, None, {"photo_id": "failed-photo"})]
    sender = HttpPhotoSender(settings)
    sent_ids = []
    monkeypatch.setattr("src.network.photo_sender.list_pending_photos", lambda _settings: records)
    monkeypatch.setattr(sender, "send", lambda record: sent_ids.append(record.metadata["photo_id"]) and False)

    for index in range(3):
        sender.send_pending(limit=1)
        records.append(PhotoRecord(None, None, {"photo_id": f"new-photo-{index}"}))

    assert sent_ids == ["failed-photo", "failed-photo", "new-photo-0"]


class FailingMqttClient:
    def connect(self, *_args, **_kwargs):
        raise OSError("network unavailable")


class CapturingMqttClient:
    def __init__(self) -> None:
        self.published = []

    def connect(self, *_args, **_kwargs):
        return None

    def publish(self, topic, payload, qos, retain):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return PublishInfo()

    def disconnect(self):
        return None


class PublishInfo:
    def wait_for_publish(self, timeout):
        self.timeout = timeout


class Response:
    def __init__(self, status_code: int, body=None) -> None:
        self.status_code = status_code
        self.body = body

    def json(self):
        if self.body is None:
            raise ValueError("no JSON body")
        return self.body


def _photo_record(tmp_path) -> PhotoRecord:
    image_path = tmp_path / "photo.jpg"
    metadata_path = tmp_path / "photo.json"
    image_path.write_bytes(b"jpeg-bytes")
    metadata = {
        "photo_id": "photo-1",
        "device_id": "edge-01",
        "captured_at_utc": "2026-06-06T10:00:00Z",
        "file_name": image_path.name,
        "sharpness_score": 12.5,
        "upload_status": "pending",
        "uploaded_at_utc": None,
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return PhotoRecord(image_path=image_path, metadata_path=metadata_path, metadata=metadata)
