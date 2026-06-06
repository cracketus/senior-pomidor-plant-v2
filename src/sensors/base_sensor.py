"""Shared helpers for sensor modules."""

from __future__ import annotations

from typing import Any


def error_reading(sensor: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"sensor": sensor, "message": message}}


def round_metric(value: Any, digits: int = 2) -> float:
    return round(float(value), digits)
