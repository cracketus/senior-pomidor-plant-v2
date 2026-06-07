import json

from src.config import load_config
from src.network.http_sender import HttpSender
from src.network.mqtt_sender import MqttSender
from src.network.photo_sender import HttpPhotoSender
from src.utils.camera import PhotoRecord


def test_mqtt_sender_returns_false_on_client_failure() -> None:
    settings = load_config({"MQTT_HOST": "core.local"})
    sender = MqttSender(settings, client_factory=lambda: FailingMqttClient())

    assert sender.publish({"hello": "world"}) is False


def test_http_sender_disabled_returns_false() -> None:
    settings = load_config({"MQTT_HOST": "core.local", "HTTP_ENABLED": "false"})

    assert HttpSender(settings).send({"hello": "world"}) is False


def test_http_photo_sender_uploads_multipart_and_marks_uploaded(tmp_path) -> None:
    settings = load_config(
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
        return Response(201)

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
    settings = load_config(
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


def test_http_photo_sender_disabled_returns_zero(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "PHOTO_UPLOAD_ENABLED": "false", "CAMERA_STORAGE_DIR": str(tmp_path)})

    assert HttpPhotoSender(settings).send_pending() == 0


class FailingMqttClient:
    def connect(self, *_args, **_kwargs):
        raise OSError("network unavailable")


class Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


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
