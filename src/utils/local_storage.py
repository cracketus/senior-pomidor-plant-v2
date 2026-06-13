"""Local telemetry persistence for offline recovery and diagnostics."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import Settings


def save_payload(
    settings: Settings,
    payload: dict[str, Any],
    logger: logging.Logger | None = None,
) -> Path | None:
    logger = logger or logging.getLogger(__name__)
    storage_dir = Path(settings.local_storage_dir)

    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        target = storage_dir / _payload_filename(settings.device_id, payload)
        temp_target = target.with_suffix(".tmp")
        temp_target.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temp_target.replace(target)
        cleanup_storage(
            storage_dir=storage_dir,
            max_age_days=settings.local_storage_max_age_days,
            max_size_mb=settings.local_storage_max_size_mb,
        )
        logger.info("Telemetry saved locally to %s", target)
        return target
    except Exception as exc:  # noqa: BLE001 - local persistence must not stop telemetry
        logger.error("Local telemetry save failed: %s", exc)
        return None


def cleanup_storage(storage_dir: Path, max_age_days: int, max_size_mb: int) -> None:
    files = sorted(storage_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    now = datetime.now(UTC)
    oldest_allowed = now - timedelta(days=max_age_days)

    for path in list(files):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified < oldest_allowed:
            path.unlink(missing_ok=True)

    remaining = sorted(storage_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    max_bytes = max_size_mb * 1024 * 1024
    total_bytes = sum(path.stat().st_size for path in remaining)

    for path in remaining:
        if total_bytes <= max_bytes:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total_bytes -= size


def list_pending_payloads(settings: Settings) -> list[Path]:
    storage_dir = Path(settings.local_storage_dir)
    if not storage_dir.exists():
        return []
    return sorted(storage_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)


def load_payload_file(path: Path, logger: logging.Logger | None = None) -> dict[str, Any] | None:
    logger = logger or logging.getLogger(__name__)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - corrupt queue files should not stop the app
        logger.error("Queued telemetry file could not be read: %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.error("Queued telemetry file does not contain a JSON object: %s", path)
        return None
    return payload


def delete_payload_file(path: Path, logger: logging.Logger | None = None) -> None:
    logger = logger or logging.getLogger(__name__)
    try:
        path.unlink(missing_ok=True)
        logger.info("Queued telemetry removed after delivery: %s", path)
    except Exception as exc:  # noqa: BLE001 - cleanup failure should not stop telemetry
        logger.error("Queued telemetry delete failed: %s: %s", path, exc)


def _payload_filename(device_id: str, payload: dict[str, Any]) -> str:
    timestamp = str(payload.get("timestamp_utc") or datetime.now(UTC).isoformat(timespec="seconds"))
    return f"{_safe_name(timestamp)}_{_safe_name(device_id)}.json"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value)
    return safe.strip("-") or "unknown"
