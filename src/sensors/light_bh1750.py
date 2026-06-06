"""BH1750 illuminance reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric


def read(address: int = 0x23, mock: bool = False) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        if mock:
            return {"light_lux": 18500.0}
        return _read_hardware(address)
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("bh1750", str(exc))


def _read_hardware(address: int) -> dict[str, float]:
    import board
    import adafruit_bh1750

    i2c = board.I2C()
    sensor = adafruit_bh1750.BH1750(i2c, address=address)
    return {"light_lux": round_metric(sensor.lux)}
