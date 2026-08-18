from types import SimpleNamespace

from scripts import maintenance_event
from src.utils.events import MAINTENANCE_COMPLETED, MAINTENANCE_STARTED


def test_maintenance_event_script_emits_start(monkeypatch) -> None:
    captured = {}
    operations = []

    settings = SimpleNamespace(watchdog_maintenance_file="maintenance.json")
    monkeypatch.setattr(maintenance_event, "load_config", lambda: settings)
    monkeypatch.setattr(maintenance_event, "configure_logger", NullLogger)
    monkeypatch.setattr(
        maintenance_event,
        "set_maintenance_hold",
        lambda path, active, reason: operations.append(("hold", path, active, reason)),
    )
    monkeypatch.setattr(
        maintenance_event,
        "emit_lifecycle_event",
        lambda settings, event_type, reason, logger: (
            operations.append(("event", event_type))
            or captured.update({"settings": settings, "event_type": event_type, "reason": reason, "logger": logger})
            or True
        ),
    )

    assert maintenance_event.main(["start", "--reason", "sensor service"]) == 0
    assert captured["event_type"] == MAINTENANCE_STARTED
    assert captured["reason"] == "sensor service"
    assert operations == [
        ("hold", "maintenance.json", True, "sensor service"),
        ("event", MAINTENANCE_STARTED),
    ]


def test_maintenance_event_script_emits_complete(monkeypatch) -> None:
    captured = {}
    operations = []

    settings = SimpleNamespace(watchdog_maintenance_file="maintenance.json")
    monkeypatch.setattr(maintenance_event, "load_config", lambda: settings)
    monkeypatch.setattr(maintenance_event, "configure_logger", NullLogger)
    monkeypatch.setattr(
        maintenance_event,
        "set_maintenance_hold",
        lambda path, active, reason: operations.append(("hold", path, active, reason)),
    )
    monkeypatch.setattr(
        maintenance_event,
        "emit_lifecycle_event",
        lambda settings, event_type, reason, logger: (
            operations.append(("event", event_type))
            or captured.update({"settings": settings, "event_type": event_type, "reason": reason, "logger": logger})
            or True
        ),
    )

    assert maintenance_event.main(["complete"]) == 0
    assert captured["event_type"] == MAINTENANCE_COMPLETED
    assert captured["reason"] is None
    assert operations == [
        ("hold", "maintenance.json", False, None),
        ("event", MAINTENANCE_COMPLETED),
    ]


def test_maintenance_event_script_returns_one_when_event_is_queued(monkeypatch) -> None:
    settings = SimpleNamespace(watchdog_maintenance_file="maintenance.json")
    monkeypatch.setattr(maintenance_event, "load_config", lambda: settings)
    monkeypatch.setattr(maintenance_event, "configure_logger", NullLogger)
    monkeypatch.setattr(maintenance_event, "set_maintenance_hold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(maintenance_event, "emit_lifecycle_event", lambda *_args, **_kwargs: False)

    assert maintenance_event.main(["start"]) == 1


def test_maintenance_event_script_refuses_transition_when_hold_write_fails(monkeypatch) -> None:
    settings = SimpleNamespace(watchdog_maintenance_file="maintenance.json")
    emitted = []
    monkeypatch.setattr(maintenance_event, "load_config", lambda: settings)
    monkeypatch.setattr(maintenance_event, "configure_logger", NullLogger)
    monkeypatch.setattr(
        maintenance_event,
        "set_maintenance_hold",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(maintenance_event, "emit_lifecycle_event", lambda *_args, **_kwargs: emitted.append(True))

    assert maintenance_event.main(["start"]) == 2
    assert emitted == []


def test_maintenance_event_script_contains_unexpected_event_failure(monkeypatch) -> None:
    settings = SimpleNamespace(watchdog_maintenance_file="maintenance.json")
    holds = []
    monkeypatch.setattr(maintenance_event, "load_config", lambda: settings)
    monkeypatch.setattr(maintenance_event, "configure_logger", NullLogger)
    monkeypatch.setattr(
        maintenance_event,
        "set_maintenance_hold",
        lambda path, active, reason: holds.append((path, active, reason)),
    )
    monkeypatch.setattr(
        maintenance_event,
        "emit_lifecycle_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unexpected sender failure")),
    )

    assert maintenance_event.main(["start", "--reason", "sensor service"]) == 2
    assert holds == [("maintenance.json", True, "sensor service")]


class NullLogger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None
