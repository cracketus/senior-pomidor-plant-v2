import pytest

from src.config import ConfigError, load_config, mqtt_topic


def test_config_requires_mqtt_host() -> None:
    with pytest.raises(ConfigError, match="MQTT_HOST"):
        load_config({})


def test_config_parses_types_and_topic() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "DEVICE_ID": "edge-01",
            "POLL_INTERVAL_SECONDS": "30",
            "MOCK_SENSORS": "true",
            "MQTT_PORT": "1884",
            "MQTT_TOPIC_PREFIX": "plants",
            "ADS1115_ADDRESS": "0x48",
            "HTTP_ENABLED": "false",
        }
    )

    assert settings.mock_sensors is True
    assert settings.ads1115_address == 0x48
    assert settings.mqtt_port == 1884
    assert mqtt_topic(settings) == "plants/edge-01/telemetry"


def test_config_parses_local_storage_settings() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "LOCAL_STORAGE_DIR": "/var/lib/senior-pomidor/telemetry",
            "LOCAL_STORAGE_MAX_AGE_DAYS": "14",
            "LOCAL_STORAGE_MAX_SIZE_MB": "128",
        },
        platform_name="Linux",
    )

    assert settings.local_storage_dir == "/var/lib/senior-pomidor/telemetry"
    assert settings.local_storage_max_age_days == 14
    assert settings.local_storage_max_size_mb == 128


def test_config_parses_camera_defaults() -> None:
    settings = load_config({"MQTT_HOST": "core.local"})

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


def test_config_parses_camera_settings() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
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
        load_config({"MQTT_HOST": "core.local", "PHOTO_UPLOAD_ENABLED": "true"})


def test_config_rejects_invalid_camera_quality() -> None:
    with pytest.raises(ConfigError, match="CAMERA_JPEG_QUALITY"):
        load_config({"MQTT_HOST": "core.local", "CAMERA_JPEG_QUALITY": "101"})


def test_config_rejects_invalid_camera_resolution() -> None:
    with pytest.raises(ConfigError, match="CAMERA_RESOLUTION"):
        load_config({"MQTT_HOST": "core.local", "CAMERA_RESOLUTION": "1920-1080"})


def test_config_rejects_invalid_camera_skip_frames() -> None:
    with pytest.raises(ConfigError, match="CAMERA_SKIP_FRAMES"):
        load_config({"MQTT_HOST": "core.local", "CAMERA_SKIP_FRAMES": "-1"})


def test_config_parses_raw_ads1115_readings_and_pod_flags() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
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
            "ADS1115_POD1_DRY_VOLTAGE": "17736",
            "ADS1115_POD1_WET_VOLTAGE": "7220",
        }
    )

    assert settings.ads1115_pod1_dry_reading == 17736
    assert settings.ads1115_pod1_wet_reading == 7220


def test_config_rejects_all_pods_disabled() -> None:
    with pytest.raises(ConfigError, match="At least one pod"):
        load_config({"MQTT_HOST": "core.local", "POD1_ENABLED": "false", "POD2_ENABLED": "false"})


def test_mock_sensors_default_to_true_on_windows() -> None:
    settings = load_config({"MQTT_HOST": "core.local"}, platform_name="Windows")

    assert settings.mock_sensors is True


def test_real_sensor_mode_is_rejected_on_windows() -> None:
    with pytest.raises(ConfigError, match="Real sensor mode"):
        load_config({"MQTT_HOST": "core.local", "MOCK_SENSORS": "false"}, platform_name="Windows")


def test_mock_sensors_default_to_false_on_linux() -> None:
    settings = load_config({"MQTT_HOST": "core.local"}, platform_name="Linux")

    assert settings.mock_sensors is False


def test_http_url_required_when_http_enabled() -> None:
    with pytest.raises(ConfigError, match="CORE_HTTP_URL"):
        load_config({"MQTT_HOST": "core.local", "HTTP_ENABLED": "true"})
