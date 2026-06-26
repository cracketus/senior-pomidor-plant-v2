import pytest

from src.utils.derived_metrics import (
    actual_vapor_pressure_kpa,
    add_vpd_metrics,
    saturation_vapor_pressure_kpa,
    vapor_pressure_deficit_kpa,
)


def test_saturation_vapor_pressure_uses_tetens_formula() -> None:
    assert saturation_vapor_pressure_kpa(20.0) == pytest.approx(2.3383, rel=0.0001)
    assert saturation_vapor_pressure_kpa(30.0) == pytest.approx(4.2431, rel=0.0001)


def test_actual_vapor_pressure_uses_relative_humidity() -> None:
    saturation = saturation_vapor_pressure_kpa(24.0)

    assert actual_vapor_pressure_kpa(saturation, 0.0) == 0.0
    assert actual_vapor_pressure_kpa(saturation, 100.0) == pytest.approx(saturation)


def test_vapor_pressure_deficit_clamps_negative_values() -> None:
    assert vapor_pressure_deficit_kpa(2.0, 2.5) == 0


def test_add_vpd_metrics_adds_air_and_leaf_metrics() -> None:
    metrics = {
        "air_temperature_c": 24.0,
        "air_humidity_percent": 58.0,
        "leaf_temp_c": 24.9,
    }

    add_vpd_metrics(metrics)

    assert metrics["air_saturation_vapor_pressure_kpa"] == 2.98
    assert metrics["air_actual_vapor_pressure_kpa"] == 1.73
    assert metrics["air_vpd_kpa"] == 1.25
    assert metrics["leaf_saturation_vapor_pressure_kpa"] == 3.15
    assert metrics["leaf_vpd_kpa"] == 1.42


def test_add_vpd_metrics_requires_air_temperature_and_humidity() -> None:
    metrics = {"air_temperature_c": 24.0}

    add_vpd_metrics(metrics)

    assert metrics == {"air_temperature_c": 24.0}
