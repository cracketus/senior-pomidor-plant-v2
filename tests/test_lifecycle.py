import json
import os

from src.config import load_config
from src.lifecycle import EVENT_REPLAY_BATCH_SIZE, emit_lifecycle_event, replay_pending_events
from src.utils.events import MAINTENANCE_COMPLETED, MAINTENANCE_STARTED


def test_emit_lifecycle_event_deletes_current_event_after_success(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_EVENT_DIR": str(tmp_path)})
    sender = FakeEventSender(results=[True])

    delivered = emit_lifecycle_event(settings, MAINTENANCE_STARTED, reason="sensor service", sender=sender)

    assert delivered is True
    assert list(tmp_path.glob("*.json")) == []
    assert sender.events[0]["event_type"] == "maintenance_started"
    assert sender.events[0]["reason"] == "sensor service"


def test_emit_lifecycle_event_keeps_current_event_after_failure(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_EVENT_DIR": str(tmp_path)})
    sender = FakeEventSender(results=[False])

    delivered = emit_lifecycle_event(settings, MAINTENANCE_COMPLETED, sender=sender)

    assert delivered is False
    saved_files = list(tmp_path.glob("*.json"))
    assert len(saved_files) == 1
    assert json.loads(saved_files[0].read_text(encoding="utf-8"))["event_type"] == "maintenance_completed"


def test_emit_lifecycle_event_replays_queued_events_before_new_event(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_EVENT_DIR": str(tmp_path)})
    queued_file = _write_queued_event(tmp_path, "queued.json", "queued", "maintenance_started")
    sender = FakeEventSender(results=[True, True])

    delivered = emit_lifecycle_event(settings, MAINTENANCE_COMPLETED, sender=sender)

    assert delivered is True
    assert not queued_file.exists()
    assert [event["event_id"] for event in sender.events] == ["queued", sender.events[1]["event_id"]]
    assert sender.events[1]["event_type"] == "maintenance_completed"


def test_replay_pending_events_stops_after_first_failure(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_EVENT_DIR": str(tmp_path)})
    failed_file = _write_queued_event(tmp_path, "failed.json", "failed", "maintenance_started")
    later_file = _write_queued_event(tmp_path, "later.json", "later", "maintenance_completed")
    os.utime(failed_file, (1, 1))
    os.utime(later_file, (2, 2))
    sender = FakeEventSender(results=[False, True])

    delivered = replay_pending_events(settings, sender)

    assert delivered == 0
    assert failed_file.exists()
    assert later_file.exists()
    assert len(sender.events) == 1


def test_replay_pending_events_skips_corrupt_file_and_continues(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_EVENT_DIR": str(tmp_path)})
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{invalid", encoding="utf-8")
    valid_file = _write_queued_event(tmp_path, "valid.json", "valid", "maintenance_completed")
    os.utime(corrupt_file, (1, 1))
    os.utime(valid_file, (2, 2))
    sender = FakeEventSender(results=[True])

    delivered = replay_pending_events(settings, sender)

    assert delivered == 1
    assert corrupt_file.exists()
    assert not valid_file.exists()


def test_replay_pending_events_processes_at_most_batch_size(tmp_path) -> None:
    settings = load_config({"MQTT_HOST": "core.local", "LOCAL_EVENT_DIR": str(tmp_path)})
    for index in range(EVENT_REPLAY_BATCH_SIZE + 1):
        event_file = _write_queued_event(tmp_path, f"{index:02d}.json", f"event-{index}", "maintenance_started")
        os.utime(event_file, (index, index))
    sender = FakeEventSender(results=[True] * (EVENT_REPLAY_BATCH_SIZE + 1))

    delivered = replay_pending_events(settings, sender)

    assert delivered == EVENT_REPLAY_BATCH_SIZE
    assert len(list(tmp_path.glob("*.json"))) == 1


class FakeEventSender:
    def __init__(self, results: list[bool]) -> None:
        self.results = iter(results)
        self.events = []

    def publish(self, event):
        self.events.append(event)
        return next(self.results)


def _write_queued_event(tmp_path, name: str, event_id: str, event_type: str):
    event = {
        "schema_version": "senior-pomidor.edge.event.v1",
        "event_id": event_id,
        "device_id": "edge-01",
        "event_type": event_type,
        "timestamp_utc": "2026-06-13T10:00:00Z",
        "source": "operator",
    }
    path = tmp_path / name
    path.write_text(json.dumps(event), encoding="utf-8")
    return path
