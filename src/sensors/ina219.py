"""INA219 bus voltage and current reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric


def read(address: int = 0x40, mock: bool = False) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        if mock:
            return {"bus_voltage_v": 3.25, "bus_current_ma": 12.4}
        return _read_hardware(address)
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("ina219", str(exc))


def _read_hardware(address: int) -> dict[str, float]:
    import board
    from adafruit_ina219 import INA219

    i2c = board.I2C()
    sensor = INA219(i2c, addr=address)
    return {
        "bus_voltage_v": round_metric(sensor.bus_voltage, 2),
        "bus_current_ma": round_metric(sensor.current, 1),
    }
