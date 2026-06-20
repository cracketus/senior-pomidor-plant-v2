"""Read selected sensors without starting the edge-node application."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.sensors import adc_ads1115, air_bme280, ina219, ir_mlx90615, light_bh1750, temp_ds18b20  # noqa: E402

SensorReader = Callable[[], dict[str, Any]]

SENSOR_NAMES = (
    "ads1115-pod1",
    "ads1115-pod2",
    "bme280-pod1",
    "bme280-pod2",
    "ds18b20-pod1",
    "ds18b20-pod2",
    "bh1750",
    "mlx90615",
    "ina219",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read selected sensors without starting the full edge-node system.")
    parser.add_argument(
        "sensors",
        nargs="*",
        choices=(*SENSOR_NAMES, "all"),
        help="Sensors to read. Use 'all' to read every configured sensor.",
    )
    parser.add_argument("--repeat", type=positive_int, default=1, help="Number of read cycles (default: 1).")
    parser.add_argument("--interval", type=non_negative_float, default=1.0, help="Seconds between cycles (default: 1).")
    parser.add_argument("--mock", action="store_true", help="Force mock readings instead of accessing hardware.")
    parser.add_argument("--list", action="store_true", help="List available sensor names and exit.")
    args = parser.parse_args(argv)

    if args.list:
        print("\n".join(SENSOR_NAMES))
        return 0
    if not args.sensors:
        parser.error("select at least one sensor, or use 'all'")

    load_dotenv()
    try:
        mock = args.mock or env_bool(os.environ, "MOCK_SENSORS", platform.system() != "Linux")
        readers = build_readers(os.environ, mock=mock)
    except ValueError as exc:
        parser.error(str(exc))

    selected = list(SENSOR_NAMES) if "all" in args.sensors else list(dict.fromkeys(args.sensors))
    return run_readers(readers, selected, repeat=args.repeat, interval=args.interval)


def build_readers(env: Mapping[str, str], *, mock: bool) -> dict[str, SensorReader]:
    ads_address = env_int(env, "ADS1115_ADDRESS", 0x48)
    return {
        "ads1115-pod1": lambda: adc_ads1115.read(
            channel=env_string(env, "ADS1115_POD1_CHANNEL", "A0"),
            dry_reading=env_float(env, "ADS1115_POD1_DRY_READING", 17736.0),
            wet_reading=env_float(env, "ADS1115_POD1_WET_READING", 7220.0),
            address=ads_address,
            mock=mock,
            pod_index=1,
        ),
        "ads1115-pod2": lambda: adc_ads1115.read(
            channel=env_string(env, "ADS1115_POD2_CHANNEL", "A1"),
            dry_reading=env_float(env, "ADS1115_POD2_DRY_READING", 17776.0),
            wet_reading=env_float(env, "ADS1115_POD2_WET_READING", 7220.0),
            address=ads_address,
            mock=mock,
            pod_index=2,
        ),
        "bme280-pod1": lambda: air_bme280.read(
            address=env_int(env, "BME280_POD1_ADDRESS", 0x76), mock=mock, pod_index=1
        ),
        "bme280-pod2": lambda: air_bme280.read(
            address=env_int(env, "BME280_POD2_ADDRESS", 0x77), mock=mock, pod_index=2
        ),
        "ds18b20-pod1": lambda: temp_ds18b20.read(rom_id=env_optional(env, "DS18B20_POD1_ROM"), mock=mock, pod_index=1),
        "ds18b20-pod2": lambda: temp_ds18b20.read(rom_id=env_optional(env, "DS18B20_POD2_ROM"), mock=mock, pod_index=2),
        "bh1750": lambda: light_bh1750.read(address=env_int(env, "BH1750_ADDRESS", 0x23), mock=mock),
        "mlx90615": lambda: ir_mlx90615.read(address=env_int(env, "MLX90615_ADDRESS", 0x5A), mock=mock),
        "ina219": lambda: ina219.read(address=env_int(env, "INA219_ADDRESS", 0x40), mock=mock),
    }


def run_readers(
    readers: Mapping[str, SensorReader],
    selected: list[str],
    *,
    repeat: int,
    interval: float,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    failed = False
    for cycle in range(1, repeat + 1):
        results: dict[str, dict[str, Any]] = {}
        for name in selected:
            try:
                reading = readers[name]()
            except Exception as exc:  # noqa: BLE001 - keep one broken sensor from hiding other results
                reading = {"error": {"sensor": name, "message": str(exc)}}
            sensor_failed = "error" in reading
            failed = failed or sensor_failed
            results[name] = {"status": "error" if sensor_failed else "ok", "reading": reading}

        print(json.dumps({"cycle": cycle, "results": results}, indent=2, sort_keys=True))
        if cycle < repeat:
            sleep(interval)
    return 1 if failed else 0


def load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as load
    except ImportError:
        return
    load()


def env_optional(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key, "").strip()
    return value or None


def env_string(env: Mapping[str, str], key: str, default: str) -> str:
    return env_optional(env, key) or default


def env_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = env_optional(env, key)
    try:
        return default if value is None else int(value, 0)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer or hexadecimal integer") from exc


def env_float(env: Mapping[str, str], key: str, default: float) -> float:
    value = env_optional(env, key)
    try:
        return default if value is None else float(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc


def env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = env_optional(env, key)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be true or false")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
