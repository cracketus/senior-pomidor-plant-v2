"""Stateful renderer for physical health indicators."""

from __future__ import annotations

import time
from collections.abc import Callable

from .adapter import IndicatorAdapter
from .model import EdgeHealthState, IndicatorPattern, pattern_for_state


class IndicatorController:
    def __init__(
        self,
        adapter: IndicatorAdapter,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = adapter
        self._sleep = sleep
        self._state = EdgeHealthState.STARTUP

    @property
    def state(self) -> EdgeHealthState:
        return self._state

    def set_state(self, state: EdgeHealthState) -> None:
        """Apply a static frame for the state.

        Animated states are initialized in their ON phase. Long-running blink/pulse
        scheduling belongs to the service loop so it cannot block acquisition.
        """
        self._state = state
        pattern = pattern_for_state(state)
        frame = IndicatorPattern(red=pattern.red, yellow=pattern.yellow, green=pattern.green)
        self._adapter.apply(frame)

    def self_test(self, *, dwell_seconds: float = 0.2) -> None:
        """Exercise every LED and restore the current state."""
        if dwell_seconds < 0:
            raise ValueError("dwell_seconds must be non-negative")
        for frame in (
            IndicatorPattern(red=True),
            IndicatorPattern(yellow=True),
            IndicatorPattern(green=True),
        ):
            self._adapter.apply(frame)
            self._sleep(dwell_seconds)
        self.set_state(self._state)

    def close(self) -> None:
        self._adapter.close()
