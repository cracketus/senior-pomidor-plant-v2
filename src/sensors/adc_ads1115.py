"""ADS1115 soil moisture reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric


CHANNEL_MAP = {"A0": "A0", "A1": "A1", "A2": "A2", "A3": "A3"}


def read(
    channel: str,
    dry_voltage: float,
    wet_voltage: float,
    address: int = 0x48,
    mock: bool = False,
    pod_index: int = 1,
) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        voltage = _mock_voltage(pod_index) if mock else _read_voltage(channel, address)
        moisture = calibrate_moisture(voltage, dry_voltage, wet_voltage)
        return {
            "voltage": round_metric(voltage),
            "soil_moisture_percent": round_metric(moisture, 1),
        }
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("ads1115", str(exc))


def calibrate_moisture(voltage: float, dry_voltage: float, wet_voltage: float) -> float:
    if dry_voltage == wet_voltage:
        raise ValueError("dry_voltage and wet_voltage must differ")
    percent = (dry_voltage - voltage) / (dry_voltage - wet_voltage) * 100.0
    return max(0.0, min(100.0, percent))


def _mock_voltage(pod_index: int) -> float:
    return 2.05 if pod_index == 1 else 1.82


def _read_voltage(channel: str, address: int) -> float:
    import board
    from adafruit_ads1x15 import ADS1115, AnalogIn, ads1x15

    channel_attr = CHANNEL_MAP[channel.upper()]
    i2c = board.I2C()
    ads = ADS1115(i2c, address=address)
    analog_channel = getattr(ads1x15.Pin, channel_attr)
    return float(AnalogIn(ads, analog_channel).voltage)
