"""Raspberry Pi camera capture, validation, and local photo metadata."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence

from src.config import Settings

PHOTO_SCHEMA_VERSION = "senior-pomidor.edge.photo.v1"

CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PhotoRecord:
    image_path: Path
    metadata_path: Path
    metadata: dict[str, object]


def capture_photo(
    settings: Settings,
    logger: logging.Logger | None = None,
    command_runner: CommandRunner | None = None,
    timestamp: datetime | None = None,
) -> PhotoRecord | None:
    """Capture one validated JPEG photo, returning its local record on success."""
    logger = logger or logging.getLogger(__name__)
    command_runner = command_runner or _run_command
    captured_at = timestamp or datetime.now(UTC)
    photo_id = _photo_id(settings.device_id, captured_at)
    storage_dir = Path(settings.camera_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    target = storage_dir / f"{photo_id}.jpg"
    metadata_path = storage_dir / f"{photo_id}.json"

    for attempt in range(1, settings.camera_max_attempts + 1):
        temp_target = storage_dir / f".{photo_id}.attempt-{attempt}.jpg"
        temp_target.unlink(missing_ok=True)

        try:
            result = command_runner(_capture_command(settings, temp_target), settings.camera_process_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - camera process failures should not stop telemetry
            logger.error("Camera capture attempt %s failed to start: %s", attempt, exc)
            temp_target.unlink(missing_ok=True)
            continue

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "unknown camera error").strip()
            logger.error("Camera capture attempt %s failed: %s", attempt, message)
            temp_target.unlink(missing_ok=True)
            continue

        validation = _validate_photo(temp_target, settings.camera_min_sharpness)
        if not validation.ok:
            logger.error("Camera capture attempt %s rejected: %s", attempt, validation.message)
            temp_target.unlink(missing_ok=True)
            continue

        temp_target.replace(target)
        metadata = _metadata(
            settings=settings,
            photo_id=photo_id,
            image_path=target,
            captured_at=captured_at,
            sharpness_score=validation.sharpness_score,
            attempts=attempt,
        )
        _write_metadata(metadata_path, metadata)
        cleanup_photo_storage(
            storage_dir=storage_dir,
            max_age_days=settings.local_storage_max_age_days,
            max_size_mb=settings.local_storage_max_size_mb,
        )
        logger.info("Camera photo saved locally to %s", target)
        return PhotoRecord(image_path=target, metadata_path=metadata_path, metadata=metadata)

    logger.error("Camera capture failed after %s attempts", settings.camera_max_attempts)
    return None


def list_pending_photos(settings: Settings) -> list[PhotoRecord]:
    storage_dir = Path(settings.camera_storage_dir)
    if not storage_dir.exists():
        return []

    records: list[PhotoRecord] = []
    for metadata_path in sorted(storage_dir.glob("*.json"), key=lambda path: path.stat().st_mtime):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict) or metadata.get("upload_status") == "uploaded":
            continue

        file_name = metadata.get("file_name")
        image_path = storage_dir / str(file_name) if file_name else metadata_path.with_suffix(".jpg")
        if image_path.exists():
            records.append(PhotoRecord(image_path=image_path, metadata_path=metadata_path, metadata=metadata))
    return records


def mark_photo_uploaded(record: PhotoRecord, timestamp: datetime | None = None) -> None:
    uploaded_at = timestamp or datetime.now(UTC)
    metadata = dict(record.metadata)
    metadata["upload_status"] = "uploaded"
    metadata["uploaded_at_utc"] = _format_timestamp(uploaded_at)
    _write_metadata(record.metadata_path, metadata)


def cleanup_photo_storage(storage_dir: Path, max_age_days: int, max_size_mb: int) -> None:
    storage_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    oldest_allowed = now - timedelta(days=max_age_days)

    records = _photo_pairs(storage_dir)
    for image_path, metadata_path in list(records):
        modified = datetime.fromtimestamp(image_path.stat().st_mtime, tz=UTC)
        if modified < oldest_allowed:
            image_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)

    records = _photo_pairs(storage_dir)
    max_bytes = max_size_mb * 1024 * 1024
    total_bytes = sum(_pair_size(image_path, metadata_path) for image_path, metadata_path in records)

    for image_path, metadata_path in records:
        if total_bytes <= max_bytes:
            break
        size = _pair_size(image_path, metadata_path)
        image_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        total_bytes -= size


def _capture_command(settings: Settings, output_path: Path) -> list[str]:
    return [
        "rpicam-still",
        "--output",
        str(output_path),
        "--nopreview",
        "--timeout",
        str(settings.camera_capture_timeout_ms),
        "--quality",
        str(settings.camera_jpeg_quality),
        "--autofocus-on-capture",
    ]


def _run_command(command: Sequence[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


@dataclass(frozen=True)
class _ValidationResult:
    ok: bool
    sharpness_score: float
    message: str


def _validate_photo(path: Path, min_sharpness: float) -> _ValidationResult:
    if not path.exists():
        return _ValidationResult(False, 0.0, "photo file was not created")
    if path.stat().st_size == 0:
        return _ValidationResult(False, 0.0, "photo file is empty")

    try:
        sharpness_score = _sharpness_score(path)
    except Exception as exc:  # noqa: BLE001 - corrupt image details are diagnostic only
        return _ValidationResult(False, 0.0, f"photo is not a readable JPEG: {exc}")

    if sharpness_score < min_sharpness:
        return _ValidationResult(
            False,
            sharpness_score,
            f"photo sharpness {sharpness_score:.2f} is below minimum {min_sharpness:.2f}",
        )
    return _ValidationResult(True, sharpness_score, "ok")


def _sharpness_score(path: Path) -> float:
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(path) as image:
        image.verify()

    with Image.open(path) as image:
        grayscale = image.convert("L")
        grayscale.thumbnail((640, 480))
        if grayscale.width < 2 or grayscale.height < 2:
            return 0.0
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        return float(ImageStat.Stat(edges).stddev[0])


def _metadata(
    settings: Settings,
    photo_id: str,
    image_path: Path,
    captured_at: datetime,
    sharpness_score: float,
    attempts: int,
) -> dict[str, object]:
    return {
        "schema_version": PHOTO_SCHEMA_VERSION,
        "photo_id": photo_id,
        "device_id": settings.device_id,
        "captured_at_utc": _format_timestamp(captured_at),
        "file_name": image_path.name,
        "file_size_bytes": image_path.stat().st_size,
        "sharpness_score": round(sharpness_score, 2),
        "attempts": attempts,
        "upload_status": "pending",
        "uploaded_at_utc": None,
    }


def _write_metadata(path: Path, metadata: dict[str, object]) -> None:
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    temp_path.replace(path)


def _photo_pairs(storage_dir: Path) -> list[tuple[Path, Path]]:
    pairs = [(image_path, image_path.with_suffix(".json")) for image_path in storage_dir.glob("*.jpg")]
    return sorted(pairs, key=lambda pair: pair[0].stat().st_mtime)


def _pair_size(image_path: Path, metadata_path: Path) -> int:
    size = image_path.stat().st_size if image_path.exists() else 0
    if metadata_path.exists():
        size += metadata_path.stat().st_size
    return size


def _photo_id(device_id: str, timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    normalized = timestamp.astimezone(UTC)
    stamp = normalized.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{_safe_name(stamp)}_{_safe_name(device_id)}"


def _format_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value)
    return safe.strip("-") or "unknown"
