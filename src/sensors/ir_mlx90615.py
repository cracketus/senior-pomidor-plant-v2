"""MLX90615 leaf temperature reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric

OBJECT_TEMPERATURE_REGISTER = 0x27


def read(address: int = 0x5A, mock: bool = False) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        if mock:
            return {"leaf_temperature_c": 24.9}
        return _read_hardware(address)
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("mlx90615", str(exc))


def _read_hardware(address: int) -> dict[str, float]:
    from smbus2 import SMBus

    with SMBus(1) as bus:
        raw = bus.read_word_data(address, OBJECT_TEMPERATURE_REGISTER)
    temperature_c = raw * 0.02 - 273.15
    return {"leaf_temperature_c": round_metric(temperature_c)}
