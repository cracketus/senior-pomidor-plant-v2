"""Typed environment configuration for the Senior Pomidor edge node."""

from __future__ import annotations

import os
import platform as platform_module
from dataclasses import dataclass
from typing import Mapping

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is optional for tests/imports
    load_dotenv = None


class ConfigError(ValueError):
    """Raised when environment configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    device_id: str
    poll_interval_seconds: int
    mock_sensors: bool
    pod1_enabled: bool
    pod2_enabled: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_topic_prefix: str
    mqtt_tls: bool
    http_enabled: bool
    core_http_url: str | None
    http_timeout_seconds: float
    local_storage_dir: str
    local_storage_max_age_days: int
    local_storage_max_size_mb: int
    ads1115_address: int
    bme280_pod1_address: int
    bme280_pod2_address: int
    bh1750_address: int
    mlx90615_address: int
    ads1115_pod1_channel: str
    ads1115_pod2_channel: str
    ads1115_pod1_dry_reading: float
    ads1115_pod1_wet_reading: float
    ads1115_pod2_dry_reading: float
    ads1115_pod2_wet_reading: float
    ds18b20_pod1_rom: str | None
    ds18b20_pod2_rom: str | None
    max_ticks: int | None


def load_config(env: Mapping[str, str] | None = None, platform_name: str | None = None) -> Settings:
    if env is None:
        if load_dotenv is not None:
            load_dotenv()
        env = os.environ
    platform_name = platform_name or platform_module.system()

    mqtt_host = _required(env, "MQTT_HOST")
    http_enabled = _bool(env, "HTTP_ENABLED", False)
    core_http_url = _optional(env, "CORE_HTTP_URL")
    if http_enabled and not core_http_url:
        raise ConfigError("CORE_HTTP_URL is required when HTTP_ENABLED=true")
    mock_sensors = _bool(env, "MOCK_SENSORS", _default_mock_sensors(platform_name))
    _validate_platform_mode(mock_sensors, platform_name)

    settings = Settings(
        device_id=_string(env, "DEVICE_ID", "balcony-edge-01"),
        poll_interval_seconds=_int(env, "POLL_INTERVAL_SECONDS", 60, minimum=1),
        mock_sensors=mock_sensors,
        pod1_enabled=_bool(env, "POD1_ENABLED", True),
        pod2_enabled=_bool(env, "POD2_ENABLED", True),
        mqtt_host=mqtt_host,
        mqtt_port=_int(env, "MQTT_PORT", 1883, minimum=1),
        mqtt_username=_optional(env, "MQTT_USERNAME"),
        mqtt_password=_optional(env, "MQTT_PASSWORD"),
        mqtt_topic_prefix=_string(env, "MQTT_TOPIC_PREFIX", "senior-pomidor").strip("/"),
        mqtt_tls=_bool(env, "MQTT_TLS", False),
        http_enabled=http_enabled,
        core_http_url=core_http_url,
        http_timeout_seconds=_float(env, "HTTP_TIMEOUT_SECONDS", 5.0, minimum=0.1),
        local_storage_dir=_string(env, "LOCAL_STORAGE_DIR", "data/telemetry"),
        local_storage_max_age_days=_int(env, "LOCAL_STORAGE_MAX_AGE_DAYS", 30, minimum=1),
        local_storage_max_size_mb=_int(env, "LOCAL_STORAGE_MAX_SIZE_MB", 256, minimum=1),
        ads1115_address=_int(env, "ADS1115_ADDRESS", 0x48, minimum=0),
        bme280_pod1_address=_int(env, "BME280_POD1_ADDRESS", 0x76, minimum=0),
        bme280_pod2_address=_int(env, "BME280_POD2_ADDRESS", 0x77, minimum=0),
        bh1750_address=_int(env, "BH1750_ADDRESS", 0x23, minimum=0),
        mlx90615_address=_int(env, "MLX90615_ADDRESS", 0x5A, minimum=0),
        ads1115_pod1_channel=_channel(env, "ADS1115_POD1_CHANNEL", "A0"),
        ads1115_pod2_channel=_channel(env, "ADS1115_POD2_CHANNEL", "A1"),
        ads1115_pod1_dry_reading=_float_alias(env, "ADS1115_POD1_DRY_READING", "ADS1115_POD1_DRY_VOLTAGE", 17736.0),
        ads1115_pod1_wet_reading=_float_alias(env, "ADS1115_POD1_WET_READING", "ADS1115_POD1_WET_VOLTAGE", 7220.0),
        ads1115_pod2_dry_reading=_float_alias(env, "ADS1115_POD2_DRY_READING", "ADS1115_POD2_DRY_VOLTAGE", 17776.0),
        ads1115_pod2_wet_reading=_float_alias(env, "ADS1115_POD2_WET_READING", "ADS1115_POD2_WET_VOLTAGE", 7220.0),
        ds18b20_pod1_rom=_optional(env, "DS18B20_POD1_ROM"),
        ds18b20_pod2_rom=_optional(env, "DS18B20_POD2_ROM"),
        max_ticks=_optional_int(env, "MAX_TICKS", minimum=1),
    )

    if not settings.pod1_enabled and not settings.pod2_enabled:
        raise ConfigError("At least one pod must be enabled")
    _validate_calibration("ADS1115_POD1", settings.ads1115_pod1_dry_reading, settings.ads1115_pod1_wet_reading)
    _validate_calibration("ADS1115_POD2", settings.ads1115_pod2_dry_reading, settings.ads1115_pod2_wet_reading)
    return settings


def mqtt_topic(settings: Settings) -> str:
    return f"{settings.mqtt_topic_prefix}/{settings.device_id}/telemetry"


def _required(env: Mapping[str, str], key: str) -> str:
    value = _optional(env, key)
    if value is None:
        raise ConfigError(f"{key} is required")
    return value


def _optional(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _string(env: Mapping[str, str], key: str, default: str) -> str:
    return _optional(env, key) or default


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _optional(env, key)
    if raw is None:
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be a boolean")


def _int(env: Mapping[str, str], key: str, default: int, minimum: int | None = None) -> int:
    raw = _optional(env, key)
    try:
        value = int(raw, 0) if raw is not None else default
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}")
    return value


def _optional_int(env: Mapping[str, str], key: str, minimum: int | None = None) -> int | None:
    raw = _optional(env, key)
    if raw is None:
        return None
    return _int(env, key, 0, minimum)


def _float(env: Mapping[str, str], key: str, default: float, minimum: float | None = None) -> float:
    raw = _optional(env, key)
    try:
        value = float(raw) if raw is not None else default
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}")
    return value


def _float_alias(env: Mapping[str, str], key: str, legacy_key: str, default: float) -> float:
    if _optional(env, key) is not None:
        return _float(env, key, default)
    return _float(env, legacy_key, default)


def _channel(env: Mapping[str, str], key: str, default: str) -> str:
    value = _string(env, key, default).upper()
    if value not in {"A0", "A1", "A2", "A3"}:
        raise ConfigError(f"{key} must be one of A0, A1, A2, A3")
    return value


def _validate_calibration(prefix: str, dry_reading: float, wet_reading: float) -> None:
    if dry_reading == wet_reading:
        raise ConfigError(f"{prefix} dry and wet calibration readings must differ")


def _default_mock_sensors(platform_name: str) -> bool:
    return platform_name.lower() != "linux"


def _validate_platform_mode(mock_sensors: bool, platform_name: str) -> None:
    if mock_sensors:
        return
    if platform_name.lower() != "linux":
        raise ConfigError(
            "Real sensor mode is only supported on Linux/Raspberry Pi. "
            "Set MOCK_SENSORS=true on Windows."
        )
