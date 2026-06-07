"""DS18B20 soil temperature reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric


def read(rom_id: str | None, mock: bool = False, pod_index: int = 1) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        if mock:
            return {"soil_temperature_c": 21.8 + pod_index * 0.3}
        if not rom_id:
            raise ValueError("DS18B20 ROM ID is not configured")
        return _read_hardware(rom_id)
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("ds18b20", str(exc))


def _read_hardware(rom_id: str) -> dict[str, float]:
    from w1thermsensor import Sensor, W1ThermSensor

    sensor = W1ThermSensor(sensor_type=Sensor.DS18B20, sensor_id=_sensor_id(rom_id))
    return {"soil_temperature_c": round_metric(sensor.get_temperature())}


def _sensor_id(rom_id: str) -> str:
    """Return the hardware id format expected by w1thermsensor."""
    return rom_id.removeprefix("28-")
