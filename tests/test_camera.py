import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

from src.config import load_config
from src.utils.camera import capture_photo, cleanup_photo_storage, list_pending_photos, mark_photo_uploaded


def test_capture_photo_saves_jpeg_and_metadata(tmp_path) -> None:
    settings = _settings(tmp_path, CAMERA_MIN_SHARPNESS="1")
    commands = []

    def runner(command, timeout):
        commands.append((command, timeout))
        _write_sharp_jpeg(Path(command[command.index("--output") + 1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    record = capture_photo(
        settings,
        command_runner=runner,
        timestamp=datetime(2026, 6, 6, 10, 0, tzinfo=UTC),
    )

    assert record is not None
    assert record.image_path.exists()
    assert record.metadata_path.exists()
    assert commands[0][0][:3] == ["rpicam-still", "--output", str(record.image_path.with_name(f".{record.image_path.stem}.attempt-1.jpg"))]
    assert "--nopreview" in commands[0][0]
    assert "--autofocus-on-capture" in commands[0][0]
    assert commands[0][1] == 20.0

    metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "senior-pomidor.edge.photo.v1"
    assert metadata["photo_id"].endswith("_edge-01")
    assert metadata["device_id"] == "edge-01"
    assert metadata["captured_at_utc"] == "2026-06-06T10:00:00Z"
    assert metadata["file_name"] == record.image_path.name
    assert metadata["file_size_bytes"] == record.image_path.stat().st_size
    assert metadata["sharpness_score"] >= 1
    assert metadata["attempts"] == 1
    assert metadata["upload_status"] == "pending"


def test_capture_photo_retries_failed_command(tmp_path) -> None:
    settings = _settings(tmp_path, CAMERA_MIN_SHARPNESS="1", CAMERA_MAX_ATTEMPTS="2")
    calls = 0

    def runner(command, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 1, "", "camera busy")
        _write_sharp_jpeg(Path(command[command.index("--output") + 1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    record = capture_photo(settings, command_runner=runner)

    assert record is not None
    assert calls == 2
    assert record.metadata["attempts"] == 2


def test_capture_photo_rejects_unreadable_file_and_retries(tmp_path) -> None:
    settings = _settings(tmp_path, CAMERA_MIN_SHARPNESS="1", CAMERA_MAX_ATTEMPTS="2")
    calls = 0

    def runner(command, _timeout):
        nonlocal calls
        calls += 1
        output = Path(command[command.index("--output") + 1])
        if calls == 1:
            output.write_text("not a jpeg", encoding="utf-8")
        else:
            _write_sharp_jpeg(output)
        return subprocess.CompletedProcess(command, 0, "", "")

    record = capture_photo(settings, command_runner=runner)

    assert record is not None
    assert calls == 2
    assert record.metadata["attempts"] == 2


def test_capture_photo_returns_none_after_low_sharpness_attempts(tmp_path) -> None:
    settings = _settings(tmp_path, CAMERA_MIN_SHARPNESS="999", CAMERA_MAX_ATTEMPTS="2")

    def runner(command, _timeout):
        _write_flat_jpeg(Path(command[command.index("--output") + 1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    record = capture_photo(settings, command_runner=runner)

    assert record is None
    assert list(tmp_path.glob("*.jpg")) == []
    assert list(tmp_path.glob("*.json")) == []


def test_pending_and_uploaded_photo_metadata(tmp_path) -> None:
    settings = _settings(tmp_path, CAMERA_MIN_SHARPNESS="1")

    def runner(command, _timeout):
        _write_sharp_jpeg(Path(command[command.index("--output") + 1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    record = capture_photo(settings, command_runner=runner)

    assert record is not None
    assert [pending.metadata_path for pending in list_pending_photos(settings)] == [record.metadata_path]

    mark_photo_uploaded(record, timestamp=datetime(2026, 6, 6, 11, 0, tzinfo=UTC))

    assert list_pending_photos(settings) == []
    metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    assert metadata["upload_status"] == "uploaded"
    assert metadata["uploaded_at_utc"] == "2026-06-06T11:00:00Z"


def test_cleanup_photo_storage_removes_old_pairs(tmp_path) -> None:
    old_image = tmp_path / "old.jpg"
    old_metadata = tmp_path / "old.json"
    fresh_image = tmp_path / "fresh.jpg"
    fresh_metadata = tmp_path / "fresh.json"
    old_image.write_text("old", encoding="utf-8")
    old_metadata.write_text("{}", encoding="utf-8")
    fresh_image.write_text("fresh", encoding="utf-8")
    fresh_metadata.write_text("{}", encoding="utf-8")
    os.utime(old_image, (1, 1))

    cleanup_photo_storage(tmp_path, max_age_days=1, max_size_mb=1)

    assert not old_image.exists()
    assert not old_metadata.exists()
    assert fresh_image.exists()
    assert fresh_metadata.exists()


def test_cleanup_photo_storage_limits_total_size(tmp_path) -> None:
    old_image = tmp_path / "old.jpg"
    old_metadata = tmp_path / "old.json"
    new_image = tmp_path / "new.jpg"
    new_metadata = tmp_path / "new.json"
    old_image.write_text("x" * 800_000, encoding="utf-8")
    old_metadata.write_text("{}", encoding="utf-8")
    new_image.write_text("x" * 800_000, encoding="utf-8")
    new_metadata.write_text("{}", encoding="utf-8")
    os.utime(old_image, (1, 1))
    os.utime(new_image, (2, 2))

    cleanup_photo_storage(tmp_path, max_age_days=36500, max_size_mb=1)

    assert not old_image.exists()
    assert not old_metadata.exists()
    assert new_image.exists()
    assert new_metadata.exists()


def _settings(tmp_path, **overrides):
    env = {
        "MQTT_HOST": "core.local",
        "DEVICE_ID": "edge-01",
        "MOCK_SENSORS": "true",
        "CAMERA_ENABLED": "true",
        "CAMERA_STORAGE_DIR": str(tmp_path),
        **overrides,
    }
    return load_config(env)


def _write_sharp_jpeg(path: Path) -> None:
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, 96, 8):
        for x in range(0, 96, 8):
            if (x + y) // 8 % 2 == 0:
                draw.rectangle((x, y, x + 7, y + 7), fill="black")
    image.save(path, "JPEG", quality=95)


def _write_flat_jpeg(path: Path) -> None:
    Image.new("RGB", (96, 96), "gray").save(path, "JPEG", quality=95)
