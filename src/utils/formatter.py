"""Senior Pomidor telemetry payload formatter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.config import Settings

SCHEMA_VERSION = "senior-pomidor.edge.telemetry.v1"


def format_payload(
    settings: Settings,
    readings: dict[str, Any],
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    timestamp = timestamp or datetime.now(UTC)
    shared = readings.get("shared", {})

    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": settings.device_id,
        "timestamp_utc": _format_timestamp(timestamp),
        "pods": {
            "pod_1": _format_pod(readings.get("pod_1", {}), shared),
            "pod_2": _format_pod(readings.get("pod_2", {}), shared),
        },
    }


def _format_pod(pod_readings: dict[str, Any] | None, shared_readings: dict[str, Any]) -> dict[str, Any]:
    if pod_readings is None:
        return {"enabled": False, "metrics": {}, "errors": []}

    metrics: dict[str, float] = {}
    errors: list[dict[str, str]] = []

    for reading in pod_readings.values():
        _merge_reading(reading, metrics, errors)
    for reading in shared_readings.values():
        _merge_reading(reading, metrics, errors)

    return {"enabled": True, "metrics": metrics, "errors": errors}


def _merge_reading(reading: Any, metrics: dict[str, float], errors: list[dict[str, str]]) -> None:
    if not isinstance(reading, dict):
        return
    error = reading.get("error")
    if isinstance(error, dict):
        errors.append(
            {
                "sensor": str(error.get("sensor", "unknown")),
                "message": str(error.get("message", "unknown sensor error")),
            }
        )
        return

    for key, value in reading.items():
        if isinstance(value, (int, float)):
            metrics[key] = float(value)


def _format_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
