from __future__ import annotations

import pytest

from src.indicator.adapter import MockIndicatorAdapter
from src.indicator.controller import IndicatorController
from src.indicator.model import EdgeHealthState, IndicatorPattern, pattern_for_state


def test_state_mapping() -> None:
    assert pattern_for_state(EdgeHealthState.OK) == IndicatorPattern(green=True)
    assert pattern_for_state(EdgeHealthState.BACKLOG) == IndicatorPattern(yellow=True, blink_hz=1.0)
    assert pattern_for_state(EdgeHealthState.DEGRADED) == IndicatorPattern(yellow=True)
    assert pattern_for_state(EdgeHealthState.MAINTENANCE) == IndicatorPattern(red=True, yellow=True)
    assert pattern_for_state(EdgeHealthState.CRITICAL) == IndicatorPattern(red=True, blink_hz=2.0)
    assert pattern_for_state(EdgeHealthState.STARTUP) == IndicatorPattern(green=True, pulse=True)


def test_controller_sets_static_frame_for_state() -> None:
    adapter = MockIndicatorAdapter()
    controller = IndicatorController(adapter)

    controller.set_state(EdgeHealthState.CRITICAL)

    assert controller.state is EdgeHealthState.CRITICAL
    assert adapter.last_pattern == IndicatorPattern(red=True)


def test_self_test_exercises_all_leds_and_restores_state() -> None:
    frames: list[IndicatorPattern] = []

    class RecordingAdapter(MockIndicatorAdapter):
        def apply(self, pattern: IndicatorPattern) -> None:
            super().apply(pattern)
            frames.append(pattern)

    adapter = RecordingAdapter()
    controller = IndicatorController(adapter, sleep=lambda _: None)
    controller.set_state(EdgeHealthState.DEGRADED)

    controller.self_test()

    assert frames[-4:] == [
        IndicatorPattern(red=True),
        IndicatorPattern(yellow=True),
        IndicatorPattern(green=True),
        IndicatorPattern(yellow=True),
    ]


def test_self_test_rejects_negative_dwell() -> None:
    controller = IndicatorController(MockIndicatorAdapter())

    with pytest.raises(ValueError, match="non-negative"):
        controller.self_test(dwell_seconds=-0.1)


def test_closed_mock_adapter_rejects_writes() -> None:
    adapter = MockIndicatorAdapter()
    adapter.close()

    with pytest.raises(RuntimeError, match="closed"):
        adapter.apply(IndicatorPattern(green=True))
