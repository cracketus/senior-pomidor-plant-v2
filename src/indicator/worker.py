"""Fault-isolated background renderer for the edge health indicator."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from src.edge_health import EdgeHealthState

from .adapter import IndicatorAdapter, MockIndicatorAdapter, RaspberryPiGpioAdapter
from .model import IndicatorPattern, pattern_for_state


class IndicatorWorker:
    def __init__(
        self,
        adapter_factory: Callable[[], IndicatorAdapter],
        *,
        enabled: bool,
        backend: str,
        startup_hz: float = 0.5,
        backlog_hz: float = 1.0,
        critical_hz: float = 2.0,
        logger: Any | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter_factory = adapter_factory
        self._enabled = enabled
        self._backend = backend
        self._frequencies = {
            EdgeHealthState.STARTUP: startup_hz,
            EdgeHealthState.BACKLOG: backlog_hz,
            EdgeHealthState.CRITICAL: critical_hz,
        }
        self._logger = logger or logging.getLogger(__name__)
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._requested = EdgeHealthState.STARTUP
        self._last_rendered: EdgeHealthState | None = None
        self._operational = False
        self._last_error: str | None = None
        self._failed = False

    def start(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="edge-health-indicator", daemon=True)
            thread = self._thread
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001 - indicator failure must not crash the collector
            with self._lock:
                if self._thread is thread:
                    self._thread = None
            self._mark_failed(exc)

    def update(self, state: EdgeHealthState) -> None:
        with self._lock:
            if state is self._requested:
                return
            self._requested = state
        self._wake.set()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "backend": self._backend,
                "requested_state": self._requested.value,
                "last_rendered_state": self._last_rendered.value if self._last_rendered else None,
                "operational": self._operational,
                "last_error": self._last_error,
            }

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self) -> None:
        adapter: IndicatorAdapter | None = None
        try:
            adapter = self._adapter_factory()
            with self._lock:
                self._operational = True
            rendered_state: EdgeHealthState | None = None
            phase_on = True
            phase_started = self._monotonic()
            while not self._stop.is_set():
                self._wake.clear()
                with self._lock:
                    requested = self._requested
                if requested is not rendered_state:
                    rendered_state = requested
                    phase_on = True
                    phase_started = self._monotonic()
                    self._apply_frame(adapter, requested, phase_on)
                frequency = self._frequencies.get(requested)
                if frequency is None:
                    self._wake.wait()
                else:
                    half_period = 1.0 / (2.0 * frequency)
                    remaining = max(0.0, half_period - (self._monotonic() - phase_started))
                    signalled = self._wake.wait(remaining)
                    if not signalled and not self._stop.is_set():
                        phase_on = not phase_on
                        phase_started = self._monotonic()
                        self._apply_frame(adapter, requested, phase_on)
        except Exception as exc:  # noqa: BLE001 - hardware boundary must not escape
            self._mark_failed(exc)
        finally:
            if adapter is not None:
                try:
                    adapter.all_off()
                except Exception as exc:  # noqa: BLE001 - best-effort shutdown
                    self._mark_failed(exc)
                try:
                    adapter.close()
                except Exception as exc:  # noqa: BLE001 - best-effort shutdown
                    self._mark_failed(exc)
            with self._lock:
                self._operational = False

    def _apply_frame(self, adapter: IndicatorAdapter, state: EdgeHealthState, phase_on: bool) -> None:
        pattern = pattern_for_state(state)
        frame = IndicatorPattern(
            red=pattern.red and phase_on,
            yellow=pattern.yellow and phase_on,
            green=pattern.green and phase_on,
        )
        adapter.apply(frame)
        with self._lock:
            self._last_rendered = state

    def _mark_failed(self, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        with self._lock:
            first_failure = not self._failed
            self._failed = True
            self._operational = False
            if self._last_error is None:
                self._last_error = message
        if first_failure:
            self._logger.error("Health indicator failed and has been disabled until restart: %s", message)


def create_indicator_worker(settings: Any, *, logger: Any | None = None) -> IndicatorWorker:
    backend = settings.indicator_backend
    if backend == "auto":
        backend = "mock" if settings.mock_sensors else "gpio"

    def adapter_factory() -> IndicatorAdapter:
        if backend == "mock":
            return MockIndicatorAdapter()
        return RaspberryPiGpioAdapter(
            red_pin=settings.indicator_red_pin,
            yellow_pin=settings.indicator_yellow_pin,
            green_pin=settings.indicator_green_pin,
        )

    return IndicatorWorker(
        adapter_factory,
        enabled=settings.indicator_enabled,
        backend=backend,
        startup_hz=settings.indicator_startup_hz,
        backlog_hz=settings.indicator_backlog_hz,
        critical_hz=settings.indicator_critical_hz,
        logger=logger,
    )
