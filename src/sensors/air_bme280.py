"""BME280 air temperature, humidity, and pressure reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric


def read(address: int, mock: bool = False, pod_index: int | None = None) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        if mock:
            return {
                "air_temperature_c": 24.0,
                "air_humidity_percent": 58.0,
                "air_pressure_hpa": 1008.5,
            }
        return _read_hardware(address)
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("bme280", str(exc))


def _read_hardware(address: int) -> dict[str, float]:
    import board
    from adafruit_bme280 import basic as adafruit_bme280

    i2c = board.I2C()
    sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=address)
    return {
        "air_temperature_c": round_metric(sensor.temperature),
        "air_humidity_percent": round_metric(sensor.relative_humidity),
        "air_pressure_hpa": round_metric(sensor.pressure),
    }
