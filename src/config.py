"""Typed environment configuration for the Senior Pomidor edge node."""

from __future__ import annotations

import importlib
import math
import os
import platform as platform_module
import re
from collections.abc import Mapping
from dataclasses import dataclass


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
    telemetry_upload_token: str | None
    telemetry_spool_db_path: str
    telemetry_spool_pending_retention_days: int
    telemetry_spool_delivered_retention_days: int
    telemetry_spool_max_payload_bytes: int
    telemetry_spool_capacity_mb: int
    telemetry_spool_batch_size: int
    telemetry_spool_rate_limit_per_second: float
    telemetry_spool_busy_timeout_ms: int
    telemetry_spool_checkpoint_interval_seconds: int
    telemetry_spool_retry_schedule_seconds: tuple[int, ...]
    telemetry_spool_retry_jitter: float
    telemetry_spool_max_attempts: int | None
    telemetry_spool_disk_warning_percent: int
    telemetry_spool_disk_degraded_percent: int
    telemetry_spool_disk_critical_percent: int
    local_storage_dir: str
    local_event_dir: str
    local_storage_max_age_days: int
    local_storage_max_size_mb: int
    camera_enabled: bool
    camera_interval_seconds: int
    camera_storage_dir: str
    camera_device: str
    camera_resolution: str
    camera_jpeg_quality: int
    camera_process_timeout_seconds: float
    camera_skip_frames: int
    camera_max_attempts: int
    camera_min_sharpness: float
    photo_upload_enabled: bool
    photo_upload_url: str | None
    photo_upload_token: str | None
    ads1115_address: int
    bme280_address: int
    bh1750_address: int
    mlx90615_address: int
    ina219_address: int
    wifi_interface: str
    wifi_profile_dir: str
    wifi_preferred_profile: str | None
    network_check_host: str
    network_dns_check_host: str
    network_recovery_status_file: str
    disk_usage_path: str
    service_name: str | None
    indicator_enabled: bool
    indicator_backend: str
    indicator_red_pin: int
    indicator_yellow_pin: int
    indicator_green_pin: int
    indicator_startup_hz: float
    indicator_backlog_hz: float
    indicator_critical_hz: float
    ads1115_pod1_channel: str
    ads1115_pod2_channel: str
    ads1115_pod1_dry_reading: float
    ads1115_pod1_wet_reading: float
    ads1115_pod2_dry_reading: float
    ads1115_pod2_wet_reading: float
    ds18b20_pod1_rom: str | None
    ds18b20_pod2_rom: str | None
    watchdog_heartbeat_file: str
    watchdog_status_file: str
    watchdog_history_file: str
    watchdog_maintenance_file: str
    watchdog_poll_seconds: int
    watchdog_timeout_seconds: int
    watchdog_startup_grace_seconds: int
    watchdog_restart_limit: int
    watchdog_restart_window_seconds: int
    watchdog_cooldown_seconds: int
    watchdog_reboot_limit: int
    watchdog_reboot_window_seconds: int
    watchdog_allow_reboot: bool
    max_ticks: int | None


def load_config(env: Mapping[str, str] | None = None, platform_name: str | None = None) -> Settings:
    if env is None:
        _load_dotenv_file()
        env = os.environ
    platform_name = platform_name or platform_module.system()

    mqtt_host = _required(env, "MQTT_HOST")
    http_enabled = _bool(env, "HTTP_ENABLED", False)
    core_http_url = _optional(env, "CORE_HTTP_URL")
    if not http_enabled or not core_http_url:
        raise ConfigError("HTTP_ENABLED=true and CORE_HTTP_URL are required for durable telemetry delivery")
    photo_upload_enabled = _bool(env, "PHOTO_UPLOAD_ENABLED", False)
    photo_upload_url = _optional(env, "PHOTO_UPLOAD_URL")
    if photo_upload_enabled and not photo_upload_url:
        raise ConfigError("PHOTO_UPLOAD_URL is required when PHOTO_UPLOAD_ENABLED=true")
    mock_sensors = _bool(env, "MOCK_SENSORS", _default_mock_sensors(platform_name))
    _validate_platform_mode(mock_sensors, platform_name)

    poll_interval_seconds = _int(env, "POLL_INTERVAL_SECONDS", 60, minimum=1)
    settings = Settings(
        device_id=_string(env, "DEVICE_ID", "balcony-edge-01"),
        poll_interval_seconds=poll_interval_seconds,
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
        telemetry_upload_token=_optional(env, "TELEMETRY_UPLOAD_TOKEN"),
        telemetry_spool_db_path=_string(env, "TELEMETRY_SPOOL_DB_PATH", "data/telemetry-spool.sqlite3"),
        telemetry_spool_pending_retention_days=_int(env, "TELEMETRY_SPOOL_PENDING_RETENTION_DAYS", 30, minimum=14),
        telemetry_spool_delivered_retention_days=_int(env, "TELEMETRY_SPOOL_DELIVERED_RETENTION_DAYS", 7, minimum=1),
        telemetry_spool_max_payload_bytes=_int(env, "TELEMETRY_SPOOL_MAX_PAYLOAD_BYTES", 16384, minimum=1024),
        telemetry_spool_capacity_mb=_int(env, "TELEMETRY_SPOOL_CAPACITY_MB", 1536, minimum=1),
        telemetry_spool_batch_size=_int(env, "TELEMETRY_SPOOL_BATCH_SIZE", 10, minimum=1),
        telemetry_spool_rate_limit_per_second=_float(env, "TELEMETRY_SPOOL_RATE_LIMIT_PER_SECOND", 2.0, minimum=0.01),
        telemetry_spool_busy_timeout_ms=_int(env, "TELEMETRY_SPOOL_BUSY_TIMEOUT_MS", 5000, minimum=1),
        telemetry_spool_checkpoint_interval_seconds=_int(
            env, "TELEMETRY_SPOOL_CHECKPOINT_INTERVAL_SECONDS", 300, minimum=1
        ),
        telemetry_spool_retry_schedule_seconds=_int_tuple(
            env, "TELEMETRY_SPOOL_RETRY_SCHEDULE_SECONDS", (5, 15, 30, 60, 300)
        ),
        telemetry_spool_retry_jitter=_float(env, "TELEMETRY_SPOOL_RETRY_JITTER", 0.2, minimum=0.0, maximum=1.0),
        telemetry_spool_max_attempts=_optional_int(env, "TELEMETRY_SPOOL_MAX_ATTEMPTS", minimum=1),
        telemetry_spool_disk_warning_percent=_int(
            env, "TELEMETRY_SPOOL_DISK_WARNING_PERCENT", 80, minimum=1, maximum=100
        ),
        telemetry_spool_disk_degraded_percent=_int(
            env, "TELEMETRY_SPOOL_DISK_DEGRADED_PERCENT", 90, minimum=1, maximum=100
        ),
        telemetry_spool_disk_critical_percent=_int(
            env, "TELEMETRY_SPOOL_DISK_CRITICAL_PERCENT", 95, minimum=1, maximum=100
        ),
        local_storage_dir=_string(env, "LOCAL_STORAGE_DIR", "data/telemetry"),
        local_event_dir=_string(env, "LOCAL_EVENT_DIR", "data/events"),
        local_storage_max_age_days=_int(env, "LOCAL_STORAGE_MAX_AGE_DAYS", 30, minimum=1),
        local_storage_max_size_mb=_int(env, "LOCAL_STORAGE_MAX_SIZE_MB", 256, minimum=1),
        camera_enabled=_bool(env, "CAMERA_ENABLED", False),
        camera_interval_seconds=_int(env, "CAMERA_INTERVAL_SECONDS", 3600, minimum=1),
        camera_storage_dir=_string(env, "CAMERA_STORAGE_DIR", "data/photos"),
        camera_device=_string(env, "CAMERA_DEVICE", "/dev/video0"),
        camera_resolution=_resolution(env, "CAMERA_RESOLUTION", "1920x1080"),
        camera_jpeg_quality=_int(env, "CAMERA_JPEG_QUALITY", 95, minimum=1, maximum=100),
        camera_process_timeout_seconds=_float(env, "CAMERA_PROCESS_TIMEOUT_SECONDS", 20.0, minimum=0.1),
        camera_skip_frames=_int(env, "CAMERA_SKIP_FRAMES", 5, minimum=0),
        camera_max_attempts=_int(env, "CAMERA_MAX_ATTEMPTS", 3, minimum=1),
        camera_min_sharpness=_float(env, "CAMERA_MIN_SHARPNESS", 6.0, minimum=0.0),
        photo_upload_enabled=photo_upload_enabled,
        photo_upload_url=photo_upload_url,
        photo_upload_token=_optional(env, "PHOTO_UPLOAD_TOKEN"),
        ads1115_address=_int(env, "ADS1115_ADDRESS", 0x48, minimum=0),
        bme280_address=_int_alias(env, "BME280_ADDRESS", "BME280_POD1_ADDRESS", 0x76, minimum=0),
        bh1750_address=_int(env, "BH1750_ADDRESS", 0x23, minimum=0),
        mlx90615_address=_int(env, "MLX90615_ADDRESS", 0x5A, minimum=0),
        ina219_address=_int(env, "INA219_ADDRESS", 0x40, minimum=0),
        wifi_interface=_string(env, "WIFI_INTERFACE", "wlan0"),
        wifi_profile_dir=_string(env, "WIFI_PROFILE_DIR", "/etc/NetworkManager/system-connections"),
        wifi_preferred_profile=_optional(env, "WIFI_PREFERRED_PROFILE"),
        network_check_host=_string(env, "NETWORK_CHECK_HOST", "1.1.1.1"),
        network_dns_check_host=_string(env, "NETWORK_DNS_CHECK_HOST", "example.com"),
        network_recovery_status_file=_string(env, "NETWORK_RECOVERY_STATUS_FILE", "data/network-recovery/status.json"),
        disk_usage_path=_string(env, "DISK_USAGE_PATH", "/"),
        service_name=_optional(env, "SERVICE_NAME"),
        indicator_enabled=_bool(env, "INDICATOR_ENABLED", False),
        indicator_backend=_choice(env, "INDICATOR_BACKEND", "auto", {"auto", "mock", "gpio"}),
        indicator_red_pin=_int(env, "INDICATOR_RED_PIN", 17, minimum=0, maximum=27),
        indicator_yellow_pin=_int(env, "INDICATOR_YELLOW_PIN", 27, minimum=0, maximum=27),
        indicator_green_pin=_int(env, "INDICATOR_GREEN_PIN", 22, minimum=0, maximum=27),
        indicator_startup_hz=_positive_finite_float(env, "INDICATOR_STARTUP_HZ", 0.5),
        indicator_backlog_hz=_positive_finite_float(env, "INDICATOR_BACKLOG_HZ", 1.0),
        indicator_critical_hz=_positive_finite_float(env, "INDICATOR_CRITICAL_HZ", 2.0),
        ads1115_pod1_channel=_channel(env, "ADS1115_POD1_CHANNEL", "A0"),
        ads1115_pod2_channel=_channel(env, "ADS1115_POD2_CHANNEL", "A1"),
        ads1115_pod1_dry_reading=_float_alias(env, "ADS1115_POD1_DRY_READING", "ADS1115_POD1_DRY_VOLTAGE", 17736.0),
        ads1115_pod1_wet_reading=_float_alias(env, "ADS1115_POD1_WET_READING", "ADS1115_POD1_WET_VOLTAGE", 7220.0),
        ads1115_pod2_dry_reading=_float_alias(env, "ADS1115_POD2_DRY_READING", "ADS1115_POD2_DRY_VOLTAGE", 17776.0),
        ads1115_pod2_wet_reading=_float_alias(env, "ADS1115_POD2_WET_READING", "ADS1115_POD2_WET_VOLTAGE", 7220.0),
        ds18b20_pod1_rom=_optional(env, "DS18B20_POD1_ROM"),
        ds18b20_pod2_rom=_optional(env, "DS18B20_POD2_ROM"),
        watchdog_heartbeat_file=_string(env, "WATCHDOG_HEARTBEAT_FILE", "data/watchdog/heartbeat.json"),
        watchdog_status_file=_string(env, "WATCHDOG_STATUS_FILE", "data/watchdog/status.json"),
        watchdog_history_file=_string(env, "WATCHDOG_HISTORY_FILE", "data/watchdog/history.json"),
        watchdog_maintenance_file=_string(env, "WATCHDOG_MAINTENANCE_FILE", "data/watchdog/maintenance.json"),
        watchdog_poll_seconds=_int(env, "WATCHDOG_POLL_SECONDS", 15, minimum=1),
        watchdog_timeout_seconds=_int(env, "WATCHDOG_TIMEOUT_SECONDS", max(180, 3 * poll_interval_seconds), minimum=1),
        watchdog_startup_grace_seconds=_int(
            env, "WATCHDOG_STARTUP_GRACE_SECONDS", max(180, 3 * poll_interval_seconds), minimum=0
        ),
        watchdog_restart_limit=_int(env, "WATCHDOG_RESTART_LIMIT", 3, minimum=1),
        watchdog_restart_window_seconds=_int(env, "WATCHDOG_RESTART_WINDOW_SECONDS", 1800, minimum=1),
        watchdog_cooldown_seconds=_int(env, "WATCHDOG_COOLDOWN_SECONDS", 300, minimum=0),
        watchdog_reboot_limit=_int(env, "WATCHDOG_REBOOT_LIMIT", 1, minimum=0),
        watchdog_reboot_window_seconds=_int(env, "WATCHDOG_REBOOT_WINDOW_SECONDS", 3600, minimum=1),
        watchdog_allow_reboot=_bool(env, "WATCHDOG_ALLOW_REBOOT", False),
        max_ticks=_optional_int(env, "MAX_TICKS", minimum=1),
    )

    if not settings.pod1_enabled and not settings.pod2_enabled:
        raise ConfigError("At least one pod must be enabled")
    if settings.watchdog_timeout_seconds <= settings.poll_interval_seconds:
        raise ConfigError("WATCHDOG_TIMEOUT_SECONDS must be greater than POLL_INTERVAL_SECONDS")
    indicator_pins = (settings.indicator_red_pin, settings.indicator_yellow_pin, settings.indicator_green_pin)
    if len(set(indicator_pins)) != len(indicator_pins):
        raise ConfigError("INDICATOR_RED_PIN, INDICATOR_YELLOW_PIN, and INDICATOR_GREEN_PIN must be unique")
    _validate_calibration("ADS1115_POD1", settings.ads1115_pod1_dry_reading, settings.ads1115_pod1_wet_reading)
    _validate_calibration("ADS1115_POD2", settings.ads1115_pod2_dry_reading, settings.ads1115_pod2_wet_reading)
    if not (
        settings.telemetry_spool_disk_warning_percent
        < settings.telemetry_spool_disk_degraded_percent
        < settings.telemetry_spool_disk_critical_percent
    ):
        raise ConfigError("Telemetry spool disk thresholds must be strictly increasing")
    samples_per_day = (86400 + settings.poll_interval_seconds - 1) // settings.poll_interval_seconds
    retained_days = settings.telemetry_spool_pending_retention_days + settings.telemetry_spool_delivered_retention_days
    required_capacity_bytes = int(settings.telemetry_spool_max_payload_bytes * samples_per_day * retained_days * 1.25)
    configured_capacity_bytes = settings.telemetry_spool_capacity_mb * 1024 * 1024
    if configured_capacity_bytes < required_capacity_bytes:
        required_mb = (required_capacity_bytes + (1024 * 1024) - 1) // (1024 * 1024)
        raise ConfigError(
            "TELEMETRY_SPOOL_CAPACITY_MB is too small for poll frequency and retention; "
            f"configure at least {required_mb} MB"
        )
    return settings


def mqtt_topic(settings: Settings) -> str:
    return f"{settings.mqtt_topic_prefix}/{settings.device_id}/telemetry"


def mqtt_event_topic(settings: Settings) -> str:
    return f"{settings.mqtt_topic_prefix}/{settings.device_id}/events"


def public_settings(settings: Settings) -> dict[str, object]:
    return {
        "device_id": settings.device_id,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "mock_sensors": settings.mock_sensors,
        "mqtt_host": settings.mqtt_host,
        "mqtt_port": settings.mqtt_port,
        "mqtt_topic_prefix": settings.mqtt_topic_prefix,
        "mqtt_tls": settings.mqtt_tls,
        "http_enabled": settings.http_enabled,
        "core_http_url": settings.core_http_url,
        "telemetry_spool_db_path": settings.telemetry_spool_db_path,
        "telemetry_spool_pending_retention_days": settings.telemetry_spool_pending_retention_days,
        "telemetry_spool_delivered_retention_days": settings.telemetry_spool_delivered_retention_days,
        "telemetry_spool_max_payload_bytes": settings.telemetry_spool_max_payload_bytes,
        "telemetry_spool_capacity_mb": settings.telemetry_spool_capacity_mb,
        "telemetry_spool_batch_size": settings.telemetry_spool_batch_size,
        "photo_upload_enabled": settings.photo_upload_enabled,
        "photo_upload_url": settings.photo_upload_url,
        "camera_enabled": settings.camera_enabled,
        "local_storage_dir": settings.local_storage_dir,
        "local_event_dir": settings.local_event_dir,
        "wifi_interface": settings.wifi_interface,
        "disk_usage_path": settings.disk_usage_path,
        "service_name": settings.service_name,
        "indicator_enabled": settings.indicator_enabled,
        "indicator_backend": settings.indicator_backend,
        "indicator_red_pin": settings.indicator_red_pin,
        "indicator_yellow_pin": settings.indicator_yellow_pin,
        "indicator_green_pin": settings.indicator_green_pin,
        "indicator_startup_hz": settings.indicator_startup_hz,
        "indicator_backlog_hz": settings.indicator_backlog_hz,
        "indicator_critical_hz": settings.indicator_critical_hz,
        "watchdog_heartbeat_file": settings.watchdog_heartbeat_file,
        "watchdog_status_file": settings.watchdog_status_file,
        "watchdog_maintenance_file": settings.watchdog_maintenance_file,
        "watchdog_timeout_seconds": settings.watchdog_timeout_seconds,
        "watchdog_allow_reboot": settings.watchdog_allow_reboot,
    }


def _load_dotenv_file() -> None:
    try:
        dotenv = importlib.import_module("dotenv")
    except ImportError:
        return
    load_dotenv = getattr(dotenv, "load_dotenv", None)
    if callable(load_dotenv):
        load_dotenv()


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


def _int(
    env: Mapping[str, str],
    key: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = _optional(env, key)
    try:
        value = int(raw, 0) if raw is not None else default
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{key} must be <= {maximum}")
    return value


def _optional_int(env: Mapping[str, str], key: str, minimum: int | None = None) -> int | None:
    raw = _optional(env, key)
    if raw is None:
        return None
    return _int(env, key, 0, minimum)


def _float(
    env: Mapping[str, str],
    key: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = _optional(env, key)
    try:
        value = float(raw) if raw is not None else default
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{key} must be <= {maximum}")
    return value


def _positive_finite_float(env: Mapping[str, str], key: str, default: float) -> float:
    value = _float(env, key, default)
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{key} must be a positive finite number")
    return value


def _choice(env: Mapping[str, str], key: str, default: str, choices: set[str]) -> str:
    value = _string(env, key, default).lower()
    if value not in choices:
        raise ConfigError(f"{key} must be one of {', '.join(sorted(choices))}")
    return value


def _int_tuple(env: Mapping[str, str], key: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = _optional(env, key)
    if raw is None:
        return default
    try:
        values = tuple(int(item.strip()) for item in raw.split(","))
    except ValueError as exc:
        raise ConfigError(f"{key} must be a comma-separated list of integers") from exc
    if not values or any(value < 0 for value in values):
        raise ConfigError(f"{key} must contain non-negative retry delays")
    return values


def _float_alias(env: Mapping[str, str], key: str, legacy_key: str, default: float) -> float:
    if _optional(env, key) is not None:
        return _float(env, key, default)
    return _float(env, legacy_key, default)


def _int_alias(
    env: Mapping[str, str],
    key: str,
    legacy_key: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if _optional(env, key) is not None:
        return _int(env, key, default, minimum, maximum)
    return _int(env, legacy_key, default, minimum, maximum)


def _channel(env: Mapping[str, str], key: str, default: str) -> str:
    value = _string(env, key, default).upper()
    if value not in {"A0", "A1", "A2", "A3"}:
        raise ConfigError(f"{key} must be one of A0, A1, A2, A3")
    return value


def _resolution(env: Mapping[str, str], key: str, default: str) -> str:
    value = _string(env, key, default).lower()
    if not re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", value):
        raise ConfigError(f"{key} must use WIDTHxHEIGHT format")
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
        raise ConfigError("Real sensor mode is only supported on Linux/Raspberry Pi. Set MOCK_SENSORS=true on Windows.")
