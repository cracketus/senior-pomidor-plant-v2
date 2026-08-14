"""Indicator output adapters.

The GPIO implementation imports RPi.GPIO lazily so development and CI can run
without Raspberry Pi hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .model import IndicatorPattern


class IndicatorAdapter(Protocol):
    def apply(self, pattern: IndicatorPattern) -> None: ...

    def all_off(self) -> None: ...

    def close(self) -> None: ...


@dataclass
class MockIndicatorAdapter:
    last_pattern: IndicatorPattern | None = None
    closed: bool = False

    def apply(self, pattern: IndicatorPattern) -> None:
        if self.closed:
            raise RuntimeError("indicator adapter is closed")
        self.last_pattern = pattern

    def all_off(self) -> None:
        self.apply(IndicatorPattern())

    def close(self) -> None:
        self.closed = True


class RaspberryPiGpioAdapter:
    """Drive three active-high LEDs using BCM GPIO numbering."""

    def __init__(self, *, red_pin: int, yellow_pin: int, green_pin: int) -> None:
        try:
            import RPi.GPIO as gpio
        except ImportError as exc:
            raise RuntimeError("RPi.GPIO is unavailable; install hardware requirements on Raspberry Pi") from exc

        self._gpio = gpio
        self._pins = {
            "red": red_pin,
            "yellow": yellow_pin,
            "green": green_pin,
        }
        self._closed = False
        gpio.setwarnings(False)
        gpio.setmode(gpio.BCM)
        for pin in self._pins.values():
            gpio.setup(pin, gpio.OUT, initial=gpio.LOW)

    def apply(self, pattern: IndicatorPattern) -> None:
        if self._closed:
            raise RuntimeError("indicator adapter is closed")
        if pattern.blink_hz is not None or pattern.pulse:
            raise ValueError("animated patterns must be rendered by IndicatorController")
        self._write(pattern)

    def write_frame(self, pattern: IndicatorPattern) -> None:
        """Write one animation frame; blink/pulse metadata is ignored."""
        if self._closed:
            raise RuntimeError("indicator adapter is closed")
        self._write(pattern)

    def _write(self, pattern: IndicatorPattern) -> None:
        values = {
            "red": pattern.red,
            "yellow": pattern.yellow,
            "green": pattern.green,
        }
        for name, enabled in values.items():
            self._gpio.output(self._pins[name], self._gpio.HIGH if enabled else self._gpio.LOW)

    def all_off(self) -> None:
        self._write(IndicatorPattern())

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.all_off()
        finally:
            self._gpio.cleanup(list(self._pins.values()))
            self._closed = True
