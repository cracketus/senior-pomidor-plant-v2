from src.config import load_config
from src.main import collect_readings


def test_collect_readings_skips_disabled_pod2() -> None:
    settings = load_config(
        {
            "MQTT_HOST": "core.local",
            "MOCK_SENSORS": "true",
            "POD2_ENABLED": "false",
        }
    )

    readings = collect_readings(settings)

    assert readings["pod_1"] is not None
    assert readings["pod_2"] is None
