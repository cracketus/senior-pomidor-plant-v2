"""Derived plant-environment telemetry metrics."""

from __future__ import annotations

import math
from typing import Any

from src.sensors.base_sensor import round_metric


def add_vpd_metrics(metrics: dict[str, float]) -> None:
    air_temperature_c = _metric(metrics, "air_temperature_c")
    air_humidity_percent = _metric(metrics, "air_humidity_percent")
    if air_temperature_c is None or air_humidity_percent is None:
        return

    air_svp = saturation_vapor_pressure_kpa(air_temperature_c)
    air_avp = actual_vapor_pressure_kpa(air_svp, air_humidity_percent)
    metrics["air_saturation_vapor_pressure_kpa"] = round_metric(air_svp)
    metrics["air_actual_vapor_pressure_kpa"] = round_metric(air_avp)
    metrics["air_vpd_kpa"] = round_metric(vapor_pressure_deficit_kpa(air_svp, air_avp))

    leaf_temperature_c = _metric(metrics, "leaf_temp_c")
    if leaf_temperature_c is None:
        return

    leaf_svp = saturation_vapor_pressure_kpa(leaf_temperature_c)
    metrics["leaf_saturation_vapor_pressure_kpa"] = round_metric(leaf_svp)
    metrics["leaf_vpd_kpa"] = round_metric(vapor_pressure_deficit_kpa(leaf_svp, air_avp))


def saturation_vapor_pressure_kpa(temperature_c: float) -> float:
    return 0.6108 * math.exp((17.27 * temperature_c) / (temperature_c + 237.3))


def actual_vapor_pressure_kpa(saturation_vapor_pressure: float, humidity_percent: float) -> float:
    return saturation_vapor_pressure * humidity_percent / 100


def vapor_pressure_deficit_kpa(saturation_vapor_pressure: float, actual_vapor_pressure: float) -> float:
    return max(saturation_vapor_pressure - actual_vapor_pressure, 0)


def _metric(metrics: dict[str, float], key: str) -> float | None:
    value: Any = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
