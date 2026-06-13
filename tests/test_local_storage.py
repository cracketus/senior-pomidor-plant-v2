import json
import os
from datetime import UTC, datetime

from src.config import load_config
from src.utils.local_storage import (
    cleanup_storage,
    delete_payload_file,
    list_pending_payloads,
    load_payload_file,
    save_payload,
)


def test_save_payload_writes_json_file(tmp_path) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "DEVICE_ID": "edge-01",
            "LOCAL_STORAGE_DIR": str(tmp_path),
        }
    )
    payload = {
        "schema_version": "senior-pomidor.edge.telemetry.v1",
        "device_id": "edge-01",
        "timestamp_utc": "2026-06-06T10:00:00Z",
        "pods": {},
    }

    saved_path = save_payload(settings, payload)

    assert saved_path is not None
    assert saved_path.exists()
    assert json.loads(saved_path.read_text(encoding="utf-8")) == payload


def test_cleanup_storage_removes_old_files(tmp_path) -> None:
    old_file = tmp_path / "old.json"
    fresh_file = tmp_path / "fresh.json"
    old_file.write_text("{}", encoding="utf-8")
    fresh_file.write_text("{}", encoding="utf-8")
    old_timestamp = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))

    cleanup_storage(tmp_path, max_age_days=1, max_size_mb=1)

    assert not old_file.exists()
    assert fresh_file.exists()


def test_cleanup_storage_limits_total_size(tmp_path) -> None:
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    old_file.write_text("x" * 800_000, encoding="utf-8")
    new_file.write_text("x" * 800_000, encoding="utf-8")
    os.utime(old_file, (1, 1))
    os.utime(new_file, (2, 2))

    cleanup_storage(tmp_path, max_age_days=36500, max_size_mb=1)

    assert not old_file.exists()
    assert new_file.exists()


def test_list_pending_payloads_returns_oldest_first(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_STORAGE_DIR": str(tmp_path)})
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    old_file.write_text("{}", encoding="utf-8")
    new_file.write_text("{}", encoding="utf-8")
    os.utime(old_file, (1, 1))
    os.utime(new_file, (2, 2))

    assert list_pending_payloads(settings) == [old_file, new_file]


def test_load_payload_file_returns_payload(tmp_path) -> None:
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"hello":"world"}', encoding="utf-8")

    assert load_payload_file(payload_file) == {"hello": "world"}


def test_load_payload_file_returns_none_for_invalid_json(tmp_path) -> None:
    payload_file = tmp_path / "payload.json"
    payload_file.write_text("{invalid", encoding="utf-8")

    assert load_payload_file(payload_file) is None


def test_delete_payload_file_removes_file(tmp_path) -> None:
    payload_file = tmp_path / "payload.json"
    payload_file.write_text("{}", encoding="utf-8")

    delete_payload_file(payload_file)

    assert not payload_file.exists()
