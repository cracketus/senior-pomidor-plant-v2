"""HTTP multipart photo uploader for edge-node camera images."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.config import Settings
from src.utils.camera import PHOTO_SCHEMA_VERSION, PhotoRecord, list_pending_photos, mark_photo_uploaded

PostFunc = Callable[..., Any]
PHOTO_REPLAY_BATCH_SIZE = 10


class HttpPhotoSender:
    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger | None = None,
        post_func: PostFunc | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self.post_func = post_func
        self._attempted_photo_ids: set[str] = set()
        self._replay_round_photo_ids: set[str] = set()

    def send_pending(
        self,
        *,
        limit: int = PHOTO_REPLAY_BATCH_SIZE,
        progress: Callable[[str], Any] | None = None,
    ) -> int:
        if not self.settings.photo_upload_enabled:
            return 0
        if not self.settings.photo_upload_url:
            self.logger.error("Photo upload is enabled but PHOTO_UPLOAD_URL is missing")
            return 0

        pending = list_pending_photos(self.settings)
        pending_ids = {self._record_id(record) for record in pending}
        self._attempted_photo_ids.intersection_update(pending_ids)
        self._replay_round_photo_ids.intersection_update(pending_ids)
        candidates = [
            record
            for record in pending
            if self._record_id(record) in self._replay_round_photo_ids
            and self._record_id(record) not in self._attempted_photo_ids
        ]
        if pending and not candidates:
            self._attempted_photo_ids.clear()
            self._replay_round_photo_ids = pending_ids
            candidates = pending

        uploaded = 0
        for record in candidates[:limit]:
            self._attempted_photo_ids.add(self._record_id(record))
            if progress is not None:
                progress(f"uploading_photo:{record.metadata.get('photo_id', 'unknown')}")
            if self.send(record):
                uploaded += 1
        return uploaded

    @staticmethod
    def _record_id(record: PhotoRecord) -> str:
        photo_id = record.metadata.get("photo_id")
        if photo_id:
            return str(photo_id)
        return str(record.metadata_path)

    def send(self, record: PhotoRecord) -> bool:
        if not self.settings.photo_upload_enabled:
            return False
        if not self.settings.photo_upload_url:
            self.logger.error("Photo upload is enabled but PHOTO_UPLOAD_URL is missing")
            return False

        metadata = record.metadata
        headers = {}
        if self.settings.photo_upload_token:
            headers["Authorization"] = f"Bearer {self.settings.photo_upload_token}"

        data = {
            "photo_id": str(metadata.get("photo_id", "")),
            "device_id": str(metadata.get("device_id", self.settings.device_id)),
            "captured_at_utc": str(metadata.get("captured_at_utc", "")),
            "schema_version": PHOTO_SCHEMA_VERSION,
        }
        if metadata.get("sharpness_score") is not None:
            data["sharpness_score"] = str(metadata["sharpness_score"])

        try:
            post = self.post_func or _requests_post
            with record.image_path.open("rb") as photo_file:
                response = post(
                    self.settings.photo_upload_url,
                    files={"photo": (record.image_path.name, photo_file, "image/jpeg")},
                    data=data,
                    headers=headers,
                    timeout=self.settings.http_timeout_seconds,
                )
            if not _is_success(response):
                self.logger.error(
                    "HTTP photo upload failed with status %s",
                    getattr(response, "status_code", "unknown"),
                )
                return False
            mark_photo_uploaded(record)
            self.logger.info("HTTP photo uploaded: %s", record.image_path)
            return True
        except Exception as exc:  # noqa: BLE001 - transport isolation boundary
            self.logger.error("HTTP photo upload failed: %s", exc)
            return False


def _requests_post(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.post(*args, **kwargs)


def _is_success(response: Any) -> bool:
    return getattr(response, "status_code", None) in {200, 202}
