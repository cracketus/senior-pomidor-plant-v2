"""MLX90614/MLX90615-compatible infrared leaf temperature reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric


def read(address: int = 0x5A, mock: bool = False) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        if mock:
            return {"ir_ambient_temp_c": 23.7, "leaf_temp_c": 24.9}
        return _read_hardware(address)
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("mlx90614", str(exc))


def _read_hardware(address: int) -> dict[str, float]:
    import adafruit_mlx90614
    import board

    i2c = board.I2C()
    sensor = adafruit_mlx90614.MLX90614(i2c, address=address)
    ambient_t = sensor.ambient_temperature
    object_t = sensor.object_temperature

    return {
        "ir_ambient_temp_c": round_metric(ambient_t),
        "leaf_temp_c": round_metric(object_t),
    }
