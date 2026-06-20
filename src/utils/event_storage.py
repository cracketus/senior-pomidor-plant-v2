"""Local lifecycle event persistence for planned maintenance visibility."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from src.config import Settings


def save_event(
    settings: Settings,
    event: dict[str, Any],
    logger: logging.Logger | None = None,
) -> Path | None:
    logger = logger or logging.getLogger(__name__)
    storage_dir = Path(settings.local_event_dir)

    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        target = storage_dir / _event_filename(event)
        temp_target = target.with_suffix(".tmp")
        temp_target.write_text(json.dumps(event, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temp_target.replace(target)
        logger.info("Lifecycle event saved locally to %s", target)
        return target
    except Exception as exc:  # noqa: BLE001 - local persistence must not stop operator workflow
        logger.error("Lifecycle event save failed: %s", exc)
        return None


def list_pending_events(settings: Settings) -> list[Path]:
    storage_dir = Path(settings.local_event_dir)
    if not storage_dir.exists():
        return []
    return sorted(storage_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)


def load_event_file(path: Path, logger: logging.Logger | None = None) -> dict[str, Any] | None:
    logger = logger or logging.getLogger(__name__)
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - corrupt queue files should not stop event replay
        logger.error("Queued lifecycle event file could not be read: %s: %s", path, exc)
        return None
    if not isinstance(event, dict):
        logger.error("Queued lifecycle event file does not contain a JSON object: %s", path)
        return None
    return event


def delete_event_file(path: Path, logger: logging.Logger | None = None) -> None:
    logger = logger or logging.getLogger(__name__)
    try:
        path.unlink(missing_ok=True)
        logger.info("Queued lifecycle event removed after delivery: %s", path)
    except Exception as exc:  # noqa: BLE001 - cleanup failure should not stop event delivery
        logger.error("Queued lifecycle event delete failed: %s: %s", path, exc)


def _event_filename(event: dict[str, Any]) -> str:
    timestamp = str(event.get("timestamp_utc", "unknown"))
    event_id = str(event.get("event_id", "unknown"))
    event_type = str(event.get("event_type", "event"))
    return f"{_safe_name(timestamp)}_{_safe_name(event_type)}_{_safe_name(event_id)}.json"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value)
    return safe.strip("-") or "unknown"
