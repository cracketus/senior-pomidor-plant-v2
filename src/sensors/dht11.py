"""DHT11 box climate reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric


def read(gpio_pin: int, mock: bool = False) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        if mock:
            return {"air_temp_c": 26.0, "air_humidity_percent": 45.0}
        return _read_hardware(gpio_pin)
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("dht11", str(exc))


def _read_hardware(gpio_pin: int) -> dict[str, float]:
    import adafruit_dht
    import board

    pin = getattr(board, f"D{gpio_pin}")
    sensor = adafruit_dht.DHT11(pin, use_pulseio=False)
    try:
        temperature = sensor.temperature
        humidity = sensor.humidity
    finally:
        sensor.exit()

    if temperature is None:
        raise RuntimeError("DHT11 temperature reading is unavailable")
    if humidity is None:
        raise RuntimeError("DHT11 humidity reading is unavailable")

    return {
        "air_temp_c": round_metric(temperature, 1),
        "air_humidity_percent": round_metric(humidity, 1),
    }
