"""GPIO-neutral health-state to indicator-pattern mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EdgeHealthState(StrEnum):
    OK = "OK"
    BACKLOG = "BACKLOG"
    DEGRADED = "DEGRADED"
    MAINTENANCE = "MAINTENANCE"
    CRITICAL = "CRITICAL"
    STARTUP = "STARTUP"


@dataclass(frozen=True)
class IndicatorPattern:
    red: bool = False
    yellow: bool = False
    green: bool = False
    blink_hz: float | None = None
    pulse: bool = False


_PATTERNS: dict[EdgeHealthState, IndicatorPattern] = {
    EdgeHealthState.OK: IndicatorPattern(green=True),
    EdgeHealthState.BACKLOG: IndicatorPattern(yellow=True, blink_hz=1.0),
    EdgeHealthState.DEGRADED: IndicatorPattern(yellow=True),
    EdgeHealthState.MAINTENANCE: IndicatorPattern(red=True, yellow=True),
    EdgeHealthState.CRITICAL: IndicatorPattern(red=True, blink_hz=2.0),
    EdgeHealthState.STARTUP: IndicatorPattern(green=True, pulse=True),
}


def pattern_for_state(state: EdgeHealthState) -> IndicatorPattern:
    return _PATTERNS[state]
