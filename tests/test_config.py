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
