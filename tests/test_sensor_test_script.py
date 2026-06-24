import json

from scripts import test_sensors


def test_run_readers_reports_success(capsys) -> None:
    readers = {
        "bme280": lambda: {"air_temperature_c": 23.5},
        "bh1750": lambda: {"light_lux": 1200.0},
    }

    result = test_sensors.run_readers(
        readers,
        ["bme280", "bh1750"],
        repeat=1,
        interval=0,
    )

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["results"]["bme280"]["status"] == "ok"
    assert output["results"]["bh1750"]["reading"]["light_lux"] == 1200.0


def test_run_readers_returns_one_when_sensor_reports_error(capsys) -> None:
    readers = {
        "bme280": lambda: {"error": {"sensor": "bme280", "message": "not detected"}},
    }

    result = test_sensors.run_readers(readers, ["bme280"], repeat=1, interval=0)

    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert output["results"]["bme280"]["status"] == "error"


def test_run_readers_repeats_and_sleeps_between_cycles(capsys) -> None:
    sleeps = []

    result = test_sensors.run_readers(
        {"ina219": lambda: {"bus_voltage_v": 3.3}},
        ["ina219"],
        repeat=3,
        interval=0.25,
        sleep=sleeps.append,
    )

    assert result == 0
    assert sleeps == [0.25, 0.25]
    assert capsys.readouterr().out.count('"cycle"') == 3


def test_build_readers_uses_environment_configuration(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        test_sensors.air_bme280,
        "read",
        lambda **kwargs: captured.update(kwargs) or {"air_temperature_c": 22.0},
    )
    readers = test_sensors.build_readers({"BME280_ADDRESS": "0x75"}, mock=False)

    assert readers["bme280"]() == {"air_temperature_c": 22.0}
    assert captured == {"address": 0x75, "mock": False}


def test_build_readers_keeps_legacy_bme280_pod1_address_as_alias(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        test_sensors.air_bme280,
        "read",
        lambda **kwargs: captured.update(kwargs) or {"air_temperature_c": 22.0},
    )
    readers = test_sensors.build_readers({"BME280_POD1_ADDRESS": "0x75"}, mock=False)

    assert readers["bme280"]() == {"air_temperature_c": 22.0}
    assert captured == {"address": 0x75, "mock": False}


def test_env_bool_rejects_invalid_value() -> None:
    try:
        test_sensors.env_bool({"MOCK_SENSORS": "sometimes"}, "MOCK_SENSORS", False)
    except ValueError as exc:
        assert str(exc) == "MOCK_SENSORS must be true or false"
    else:
        raise AssertionError("Expected invalid boolean to fail")
