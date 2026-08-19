import pytest

from src.config import ConfigError, load_config, mqtt_event_topic, mqtt_topic


def test_config_requires_mqtt_host() -> None:
    with pytest.raises(ConfigError, match="MQTT_HOST"):
        load_config({})


def test_config_parses_types_and_topic() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "DEVICE_ID": "edge-01",
            "POLL_INTERVAL_SECONDS": "30",
            "TELEMETRY_SPOOL_CAPACITY_MB": "3072",
            "MOCK_SENSORS": "true",
            "MQTT_PORT": "1884",
            "MQTT_TOPIC_PREFIX": "plants",
            "ADS1115_ADDRESS": "0x48",
            "BME280_ADDRESS": "0x75",
        }
    )

    assert settings.mock_sensors is True
    assert settings.ads1115_address == 0x48
    assert settings.bme280_address == 0x75
    assert settings.mqtt_port == 1884
    assert mqtt_topic(settings) == "plants/edge-01/telemetry"
    assert mqtt_event_topic(settings) == "plants/edge-01/events"


def test_config_parses_local_storage_settings() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "LOCAL_STORAGE_DIR": "/var/lib/senior-pomidor/telemetry",
            "LOCAL_EVENT_DIR": "/var/lib/senior-pomidor/events",
            "LOCAL_STORAGE_MAX_AGE_DAYS": "14",
            "LOCAL_STORAGE_MAX_SIZE_MB": "128",
            "WATCHDOG_MAINTENANCE_FILE": "/var/lib/senior-pomidor/watchdog/maintenance.json",
        },
        platform_name="Linux",
    )

    assert settings.local_storage_dir == "/var/lib/senior-pomidor/telemetry"
    assert settings.local_event_dir == "/var/lib/senior-pomidor/events"
    assert settings.local_storage_max_age_days == 14
    assert settings.local_storage_max_size_mb == 128
    assert settings.watchdog_maintenance_file == "/var/lib/senior-pomidor/watchdog/maintenance.json"


def test_config_parses_camera_defaults() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
        }
    )

    assert settings.camera_enabled is False
    assert settings.camera_interval_seconds == 3600
    assert settings.camera_storage_dir == "data/photos"
    assert settings.camera_device == "/dev/video0"
    assert settings.camera_resolution == "1920x1080"
    assert settings.camera_jpeg_quality == 95
    assert settings.camera_process_timeout_seconds == 20.0
    assert settings.camera_skip_frames == 5
    assert settings.camera_max_attempts == 3
    assert settings.camera_min_sharpness == 6.0
    assert settings.photo_upload_enabled is False
    assert settings.photo_upload_url is None
    assert settings.photo_upload_token is None


def test_config_parses_health_defaults() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
        }
    )

    assert settings.ina219_address == 0x40
    assert settings.wifi_interface == "wlan0"
    assert settings.wifi_profile_dir == "/etc/NetworkManager/system-connections"
    assert settings.wifi_preferred_profile is None
    assert settings.network_check_host == "1.1.1.1"
    assert settings.network_dns_check_host == "example.com"
    assert settings.network_recovery_status_file == "data/network-recovery/status.json"
    assert settings.disk_usage_path == "/"
    assert settings.service_name is None
    assert settings.indicator_enabled is False
    assert settings.indicator_backend == "auto"
    assert (settings.indicator_red_pin, settings.indicator_yellow_pin, settings.indicator_green_pin) == (17, 27, 22)
    assert (settings.indicator_startup_hz, settings.indicator_backlog_hz, settings.indicator_critical_hz) == (
        0.5,
        1.0,
        2.0,
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("INDICATOR_BACKEND", "invalid"),
        ("INDICATOR_RED_PIN", "28"),
        ("INDICATOR_STARTUP_HZ", "0"),
        ("INDICATOR_BACKLOG_HZ", "nan"),
        ("INDICATOR_CRITICAL_HZ", "inf"),
    ],
)
def test_config_rejects_invalid_indicator_settings(key, value) -> None:
    with pytest.raises(ConfigError, match=key):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                key: value,
            }
        )


def test_config_rejects_duplicate_indicator_pins() -> None:
    with pytest.raises(ConfigError, match="must be unique"):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                "INDICATOR_RED_PIN": "17",
                "INDICATOR_GREEN_PIN": "17",
            }
        )


def test_config_parses_indicator_settings() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "INDICATOR_ENABLED": "true",
            "INDICATOR_BACKEND": "mock",
            "INDICATOR_RED_PIN": "5",
            "INDICATOR_YELLOW_PIN": "6",
            "INDICATOR_GREEN_PIN": "13",
            "INDICATOR_STARTUP_HZ": "0.25",
            "INDICATOR_BACKLOG_HZ": "1.5",
            "INDICATOR_CRITICAL_HZ": "4",
        }
    )

    assert settings.indicator_enabled is True
    assert settings.indicator_backend == "mock"
    assert (settings.indicator_red_pin, settings.indicator_yellow_pin, settings.indicator_green_pin) == (5, 6, 13)
    assert (settings.indicator_startup_hz, settings.indicator_backlog_hz, settings.indicator_critical_hz) == (
        0.25,
        1.5,
        4.0,
    )


def test_config_parses_health_settings() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "INA219_ADDRESS": "0x41",
            "WIFI_INTERFACE": "wlan1",
            "WIFI_PROFILE_DIR": "/nm/profiles",
            "WIFI_PREFERRED_PROFILE": "greenhouse",
            "NETWORK_CHECK_HOST": "8.8.8.8",
            "NETWORK_DNS_CHECK_HOST": "core.local",
            "NETWORK_RECOVERY_STATUS_FILE": "/app/data/network/status.json",
            "DISK_USAGE_PATH": "/app/data",
            "SERVICE_NAME": "senior-pomidor-edge",
        }
    )

    assert settings.ina219_address == 0x41
    assert settings.wifi_interface == "wlan1"
    assert settings.wifi_profile_dir == "/nm/profiles"
    assert settings.wifi_preferred_profile == "greenhouse"
    assert settings.network_check_host == "8.8.8.8"
    assert settings.network_dns_check_host == "core.local"
    assert settings.network_recovery_status_file == "/app/data/network/status.json"
    assert settings.disk_usage_path == "/app/data"
    assert settings.service_name == "senior-pomidor-edge"


def test_config_parses_camera_settings() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "CAMERA_ENABLED": "true",
            "CAMERA_INTERVAL_SECONDS": "120",
            "CAMERA_STORAGE_DIR": "/var/lib/senior-pomidor/photos",
            "CAMERA_DEVICE": "/dev/video2",
            "CAMERA_RESOLUTION": "1280x720",
            "CAMERA_JPEG_QUALITY": "90",
            "CAMERA_PROCESS_TIMEOUT_SECONDS": "30",
            "CAMERA_SKIP_FRAMES": "2",
            "CAMERA_MAX_ATTEMPTS": "5",
            "CAMERA_MIN_SHARPNESS": "8.5",
            "PHOTO_UPLOAD_ENABLED": "true",
            "PHOTO_UPLOAD_URL": "https://core.example/photos",
            "PHOTO_UPLOAD_TOKEN": "secret",
        }
    )

    assert settings.camera_enabled is True
    assert settings.camera_interval_seconds == 120
    assert settings.camera_storage_dir == "/var/lib/senior-pomidor/photos"
    assert settings.camera_device == "/dev/video2"
    assert settings.camera_resolution == "1280x720"
    assert settings.camera_jpeg_quality == 90
    assert settings.camera_process_timeout_seconds == 30.0
    assert settings.camera_skip_frames == 2
    assert settings.camera_max_attempts == 5
    assert settings.camera_min_sharpness == 8.5
    assert settings.photo_upload_enabled is True
    assert settings.photo_upload_url == "https://core.example/photos"
    assert settings.photo_upload_token == "secret"


def test_photo_upload_url_required_when_upload_enabled() -> None:
    with pytest.raises(ConfigError, match="PHOTO_UPLOAD_URL"):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                "PHOTO_UPLOAD_ENABLED": "true",
            }
        )


def test_config_rejects_invalid_camera_quality() -> None:
    with pytest.raises(ConfigError, match="CAMERA_JPEG_QUALITY"):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                "CAMERA_JPEG_QUALITY": "101",
            }
        )


def test_config_rejects_invalid_camera_resolution() -> None:
    with pytest.raises(ConfigError, match="CAMERA_RESOLUTION"):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                "CAMERA_RESOLUTION": "1920-1080",
            }
        )


def test_config_rejects_invalid_camera_skip_frames() -> None:
    with pytest.raises(ConfigError, match="CAMERA_SKIP_FRAMES"):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                "CAMERA_SKIP_FRAMES": "-1",
            }
        )


def test_config_parses_raw_ads1115_readings_and_pod_flags() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "POD2_ENABLED": "false",
            "ADS1115_POD1_DRY_READING": "17736",
            "ADS1115_POD1_WET_READING": "7220",
            "ADS1115_POD2_DRY_READING": "17776",
            "ADS1115_POD2_WET_READING": "7220",
        }
    )

    assert settings.pod2_enabled is False
    assert settings.ads1115_pod1_dry_reading == 17736
    assert settings.ads1115_pod1_wet_reading == 7220
    assert settings.ads1115_pod2_dry_reading == 17776
    assert settings.ads1115_pod2_wet_reading == 7220


def test_config_keeps_legacy_ads1115_voltage_env_names_as_aliases() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "ADS1115_POD1_DRY_VOLTAGE": "17736",
            "ADS1115_POD1_WET_VOLTAGE": "7220",
        }
    )

    assert settings.ads1115_pod1_dry_reading == 17736
    assert settings.ads1115_pod1_wet_reading == 7220


def test_config_keeps_legacy_bme280_pod1_address_as_alias() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "BME280_POD1_ADDRESS": "0x75",
        }
    )

    assert settings.bme280_address == 0x75


def test_config_rejects_all_pods_disabled() -> None:
    with pytest.raises(ConfigError, match="At least one pod"):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                "POD1_ENABLED": "false",
                "POD2_ENABLED": "false",
            }
        )


def test_mock_sensors_default_to_true_on_windows() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
        },
        platform_name="Windows",
    )

    assert settings.mock_sensors is True


def test_real_sensor_mode_is_rejected_on_windows() -> None:
    with pytest.raises(ConfigError, match="Real sensor mode"):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                "MOCK_SENSORS": "false",
            },
            platform_name="Windows",
        )


def test_mock_sensors_default_to_false_on_linux() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
        },
        platform_name="Linux",
    )

    assert settings.mock_sensors is False


def test_http_url_required_when_http_enabled() -> None:
    with pytest.raises(ConfigError, match="CORE_HTTP_URL"):
        load_config({"MQTT_HOST": "core.local", "HTTP_ENABLED": "true"})


def test_watchdog_defaults_scale_with_poll_interval_and_disable_reboot() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "true",
            "CORE_HTTP_URL": "https://core.example/telemetry",
            "POLL_INTERVAL_SECONDS": "90",
        }
    )

    assert settings.watchdog_timeout_seconds == 270
    assert settings.watchdog_restart_limit == 3
    assert settings.watchdog_restart_window_seconds == 1800
    assert settings.watchdog_cooldown_seconds == 300
    assert settings.watchdog_reboot_limit == 1
    assert settings.watchdog_reboot_window_seconds == 3600
    assert settings.watchdog_allow_reboot is False


@pytest.mark.parametrize("timeout", [59, 60])
def test_watchdog_timeout_must_exceed_collection_poll_interval(timeout) -> None:
    with pytest.raises(ConfigError, match="WATCHDOG_TIMEOUT_SECONDS must be greater than POLL_INTERVAL_SECONDS"):
        load_config(
            {
                "MQTT_HOST": "core.local",
                "HTTP_ENABLED": "true",
                "CORE_HTTP_URL": "https://core.example/telemetry",
                "POLL_INTERVAL_SECONDS": "60",
                "WATCHDOG_TIMEOUT_SECONDS": str(timeout),
            }
        )
