"""Optional HTTP telemetry sender."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.config import Settings

PostFunc = Callable[..., Any]


class HttpSender:
    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger | None = None,
        post_func: PostFunc | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self.post_func = post_func

    def send(self, payload: dict[str, Any]) -> bool:
        if not self.settings.http_enabled:
            return False
        if not self.settings.core_http_url:
            self.logger.error("HTTP fallback is enabled but CORE_HTTP_URL is missing")
            return False

        try:
            post = self.post_func or _requests_post
            response = post(
                self.settings.core_http_url,
                json=payload,
                timeout=self.settings.http_timeout_seconds,
            )
            if getattr(response, "status_code", None) != 202:
                if getattr(response, "status_code", None) == 400:
                    self.logger.error("HTTP telemetry rejected with 400: %s", _response_detail(response))
                else:
                    self.logger.error(
                        "HTTP telemetry failed with status %s: %s",
                        getattr(response, "status_code", "unknown"),
                        _response_detail(response),
                    )
                return False
            self.logger.info("HTTP telemetry delivered")
            return True
        except Exception as exc:  # noqa: BLE001 - transport isolation boundary
            self.logger.error("HTTP telemetry failed: %s", exc)
            return False


def _requests_post(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.post(*args, **kwargs)


def _response_detail(response: Any) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - optional response compatibility
        body = getattr(response, "text", "")
    return str(body)
