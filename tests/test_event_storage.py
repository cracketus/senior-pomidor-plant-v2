import json
import os

from src.config import load_config
from src.utils.event_storage import delete_event_file, list_pending_events, load_event_file, save_event


def test_save_event_writes_json_file(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_EVENT_DIR": str(tmp_path)})
    event = _event("event-1")

    saved_path = save_event(settings, event)

    assert saved_path is not None
    assert saved_path.exists()
    assert json.loads(saved_path.read_text(encoding="utf-8")) == event


def test_list_pending_events_returns_oldest_first(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_EVENT_DIR": str(tmp_path)})
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    old_file.write_text("{}", encoding="utf-8")
    new_file.write_text("{}", encoding="utf-8")
    os.utime(old_file, (1, 1))
    os.utime(new_file, (2, 2))

    assert list_pending_events(settings) == [old_file, new_file]


def test_load_event_file_returns_event(tmp_path) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text('{"event_id":"event-1"}', encoding="utf-8")

    assert load_event_file(event_file) == {"event_id": "event-1"}


def test_load_event_file_returns_none_for_invalid_json(tmp_path) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text("{invalid", encoding="utf-8")

    assert load_event_file(event_file) is None


def test_delete_event_file_removes_file(tmp_path) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text("{}", encoding="utf-8")

    delete_event_file(event_file)

    assert not event_file.exists()


def _event(event_id: str) -> dict[str, str]:
    return {
        "schema_version": "senior-pomidor.edge.event.v1",
        "event_id": event_id,
        "device_id": "edge-01",
        "event_type": "maintenance_started",
        "timestamp_utc": "2026-06-13T10:00:00Z",
        "source": "operator",
    }
