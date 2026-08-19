from __future__ import annotations

import time

import pytest

from src.config import load_config
from src.edge_health import EdgeHealthState
from src.indicator.adapter import MockIndicatorAdapter
from src.indicator.model import IndicatorPattern
from src.indicator.worker import IndicatorWorker, create_indicator_worker


def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not reached")
        time.sleep(0.001)


def test_worker_renders_transition_immediately_and_shuts_down_all_off() -> None:
    frames: list[IndicatorPattern] = []

    class Adapter(MockIndicatorAdapter):
        def apply(self, pattern: IndicatorPattern) -> None:
            super().apply(pattern)
            frames.append(pattern)

    adapter = Adapter()
    worker = IndicatorWorker(lambda: adapter, enabled=True, backend="mock")
    worker.start()
    wait_until(lambda: frames == [IndicatorPattern(green=True)])
    worker.update(EdgeHealthState.DEGRADED)
    wait_until(lambda: frames[-1] == IndicatorPattern(yellow=True))
    worker.stop()

    assert frames[-1] == IndicatorPattern()
    assert adapter.closed is True
    assert worker.snapshot()["operational"] is False


def test_worker_animated_states_toggle_at_configured_frequency() -> None:
    frames: list[IndicatorPattern] = []

    class Adapter(MockIndicatorAdapter):
        def apply(self, pattern: IndicatorPattern) -> None:
            super().apply(pattern)
            frames.append(pattern)

    worker = IndicatorWorker(lambda: Adapter(), enabled=True, backend="mock", critical_hz=100.0)
    worker.update(EdgeHealthState.CRITICAL)
    worker.start()
    wait_until(lambda: frames[:2] == [IndicatorPattern(red=True), IndicatorPattern()])
    worker.stop()


def test_worker_failure_is_contained_and_logged_once() -> None:
    messages: list[tuple] = []

    class Logger:
        def error(self, *args) -> None:
            messages.append(args)

    def fail_factory():
        raise ImportError("no GPIO")

    worker = IndicatorWorker(fail_factory, enabled=True, backend="gpio", logger=Logger())
    worker.start()
    wait_until(lambda: worker.snapshot()["last_error"] is not None)
    worker.stop()

    assert len(messages) == 1
    assert worker.snapshot()["operational"] is False


def test_thread_start_failure_is_contained_and_stop_remains_safe(monkeypatch) -> None:
    messages: list[tuple] = []

    class Logger:
        def error(self, *args) -> None:
            messages.append(args)

    def fail_start(_thread) -> None:
        raise RuntimeError("thread capacity exhausted")

    monkeypatch.setattr("src.indicator.worker.threading.Thread.start", fail_start)
    worker = IndicatorWorker(MockIndicatorAdapter, enabled=True, backend="mock", logger=Logger())

    worker.start()
    worker.stop()

    assert len(messages) == 1
    assert "thread capacity exhausted" in str(worker.snapshot()["last_error"])
    assert worker.snapshot()["operational"] is False


def test_write_and_cleanup_failures_are_contained_with_one_log() -> None:
    messages: list[tuple] = []

    class Logger:
        def error(self, *args) -> None:
            messages.append(args)

    class Adapter:
        def apply(self, _pattern) -> None:
            raise OSError("write failed")

        def all_off(self) -> None:
            raise OSError("all off failed")

        def close(self) -> None:
            raise OSError("cleanup failed")

    worker = IndicatorWorker(Adapter, enabled=True, backend="gpio", logger=Logger())
    worker.start()
    wait_until(lambda: worker.snapshot()["last_error"] is not None)
    worker.stop()

    assert len(messages) == 1
    assert "write failed" in str(worker.snapshot()["last_error"])


def test_disabled_worker_tracks_requested_state_without_initializing_adapter() -> None:
    worker = IndicatorWorker(lambda: (_ for _ in ()).throw(AssertionError()), enabled=False, backend="gpio")
    worker.start()
    worker.update(EdgeHealthState.OK)
    worker.stop()

    assert worker.snapshot() == {
        "enabled": False,
        "backend": "gpio",
        "requested_state": "OK",
        "last_rendered_state": None,
        "operational": False,
        "last_error": None,
    }


@pytest.mark.parametrize(
    ("mock_sensors", "backend", "expected"),
    [("true", "auto", "mock"), ("true", "gpio", "gpio"), ("true", "mock", "mock")],
)
def test_factory_resolves_backend(mock_sensors: str, backend: str, expected: str) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "MOCK_SENSORS": mock_sensors,
            "INDICATOR_BACKEND": backend,
        }
    )

    assert create_indicator_worker(settings).snapshot()["backend"] == expected
