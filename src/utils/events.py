"""Lifecycle event payload formatting."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from src.config import Settings

EVENT_SCHEMA_VERSION = "senior-pomidor.edge.event.v1"
MAINTENANCE_STARTED = "maintenance_started"
MAINTENANCE_COMPLETED = "maintenance_completed"
SUPPORTED_EVENT_TYPES = {MAINTENANCE_STARTED, MAINTENANCE_COMPLETED}


def format_lifecycle_event(
    settings: Settings,
    event_type: str,
    *,
    reason: str | None = None,
    source: str = "operator",
    timestamp: datetime | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ValueError(f"Unsupported lifecycle event type: {event_type}")

    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": event_id or str(uuid.uuid4()),
        "device_id": settings.device_id,
        "event_type": event_type,
        "timestamp_utc": _format_timestamp(timestamp or datetime.now(UTC)),
        "source": source,
    }
    if reason:
        event["reason"] = reason
    return event


def _format_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
