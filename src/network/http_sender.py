"""Optional HTTP telemetry sender."""

from __future__ import annotations

import logging
from typing import Any

from src.config import Settings


class HttpSender:
    def __init__(self, settings: Settings, logger: logging.Logger | None = None) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)

    def send(self, payload: dict[str, Any]) -> bool:
        if not self.settings.http_enabled:
            return False
        if not self.settings.core_http_url:
            self.logger.error("HTTP fallback is enabled but CORE_HTTP_URL is missing")
            return False

        try:
            import requests

            response = requests.post(
                self.settings.core_http_url,
                json=payload,
                timeout=self.settings.http_timeout_seconds,
            )
            response.raise_for_status()
            self.logger.info("HTTP telemetry delivered")
            return True
        except Exception as exc:  # noqa: BLE001 - transport isolation boundary
            self.logger.error("HTTP telemetry failed: %s", exc)
            return False
