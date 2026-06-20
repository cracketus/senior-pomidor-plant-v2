from scripts import maintenance_event
from src.utils.events import MAINTENANCE_COMPLETED, MAINTENANCE_STARTED


def test_maintenance_event_script_emits_start(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(maintenance_event, "load_config", lambda: object())
    monkeypatch.setattr(maintenance_event, "configure_logger", NullLogger)
    monkeypatch.setattr(
        maintenance_event,
        "emit_lifecycle_event",
        lambda settings, event_type, reason, logger: (
            captured.update({"settings": settings, "event_type": event_type, "reason": reason, "logger": logger})
            or True
        ),
    )

    assert maintenance_event.main(["start", "--reason", "sensor service"]) == 0
    assert captured["event_type"] == MAINTENANCE_STARTED
    assert captured["reason"] == "sensor service"


def test_maintenance_event_script_emits_complete(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(maintenance_event, "load_config", lambda: object())
    monkeypatch.setattr(maintenance_event, "configure_logger", NullLogger)
    monkeypatch.setattr(
        maintenance_event,
        "emit_lifecycle_event",
        lambda settings, event_type, reason, logger: (
            captured.update({"settings": settings, "event_type": event_type, "reason": reason, "logger": logger})
            or True
        ),
    )

    assert maintenance_event.main(["complete"]) == 0
    assert captured["event_type"] == MAINTENANCE_COMPLETED
    assert captured["reason"] is None


def test_maintenance_event_script_returns_one_when_event_is_queued(monkeypatch) -> None:
    monkeypatch.setattr(maintenance_event, "load_config", lambda: object())
    monkeypatch.setattr(maintenance_event, "configure_logger", NullLogger)
    monkeypatch.setattr(maintenance_event, "emit_lifecycle_event", lambda *_args, **_kwargs: False)

    assert maintenance_event.main(["start"]) == 1


class NullLogger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None
