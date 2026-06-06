"""ADS1115 soil moisture reader."""

from __future__ import annotations

from .base_sensor import error_reading, round_metric


CHANNEL_MAP = {"A0": "A0", "A1": "A1", "A2": "A2", "A3": "A3"}


def read(
    channel: str,
    dry_reading: float,
    wet_reading: float,
    address: int = 0x48,
    mock: bool = False,
    pod_index: int = 1,
) -> dict[str, float] | dict[str, dict[str, str]]:
    try:
        raw_reading = _mock_raw_reading(pod_index) if mock else _read_raw_reading(channel, address)
        moisture = calibrate_moisture(raw_reading, dry_reading, wet_reading)
        return {
            "adc_raw": round_metric(raw_reading, 0),
            "soil_moisture_percent": round_metric(moisture, 1),
        }
    except Exception as exc:  # noqa: BLE001 - sensor isolation boundary
        return error_reading("ads1115", str(exc))


def calibrate_moisture(raw_reading: float, dry_reading: float, wet_reading: float) -> float:
    if dry_reading == wet_reading:
        raise ValueError("dry_reading and wet_reading must differ")
    percent = (dry_reading - raw_reading) / (dry_reading - wet_reading) * 100.0
    return max(0.0, min(100.0, percent))


def _mock_raw_reading(pod_index: int) -> float:
    return 12500.0 if pod_index == 1 else 11800.0


def _read_raw_reading(channel: str, address: int) -> float:
    import board
    from adafruit_ads1x15 import ADS1115, AnalogIn, ads1x15

    channel_attr = CHANNEL_MAP[channel.upper()]
    i2c = board.I2C()
    ads = ADS1115(i2c, address=address)
    analog_channel = getattr(ads1x15.Pin, channel_attr)
    return float(AnalogIn(ads, analog_channel).value)
