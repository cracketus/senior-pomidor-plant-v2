"""Optional HTTP telemetry sender."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.config import Settings
from src.telemetry_spool import DeliveryErrorCode, DeliveryResult, DeliveryStatus

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

    def send(self, payload: dict[str, Any]) -> DeliveryResult:
        record_id = payload.get("record_id")
        if not self.settings.http_enabled:
            return DeliveryResult(
                DeliveryStatus.RETRY,
                None,
                "HTTP telemetry delivery is disabled",
                error_code=DeliveryErrorCode.DELIVERY_DISABLED.value,
            )
        if not self.settings.core_http_url:
            return DeliveryResult(
                DeliveryStatus.RETRY,
                None,
                "CORE_HTTP_URL is missing",
                error_code=DeliveryErrorCode.MISSING_URL.value,
            )

        try:
            post = self.post_func or _requests_post
            kwargs: dict[str, Any] = {
                "json": payload,
                "timeout": self.settings.http_timeout_seconds,
            }
            if self.settings.telemetry_upload_token:
                kwargs["headers"] = {"Authorization": f"Bearer {self.settings.telemetry_upload_token}"}
            response = post(self.settings.core_http_url, **kwargs)
            status_code = getattr(response, "status_code", None)
            try:
                body = response.json()
            except Exception:  # noqa: BLE001 - invalid ack is retryable
                body = None
            if not isinstance(body, dict):
                return DeliveryResult(
                    DeliveryStatus.RETRY,
                    None,
                    f"HTTP {status_code}: {_response_detail(response)}",
                    status_code,
                    DeliveryErrorCode.INVALID_RESPONSE.value,
                )
            try:
                status = DeliveryStatus(str(body.get("status")))
            except ValueError:
                return DeliveryResult(
                    DeliveryStatus.RETRY,
                    None,
                    "invalid acknowledgement status",
                    status_code,
                    DeliveryErrorCode.INVALID_ACK_STATUS.value,
                )
            ack_record_id = body.get("record_id")
            if not isinstance(ack_record_id, str) or ack_record_id != record_id:
                return DeliveryResult(
                    DeliveryStatus.RETRY,
                    ack_record_id if isinstance(ack_record_id, str) else None,
                    "missing or mismatched record_id in acknowledgement",
                    status_code,
                    DeliveryErrorCode.ACK_RECORD_ID_MISMATCH.value,
                )
            if status_code in {200, 202}:
                detail = body.get("detail")
                return DeliveryResult(
                    status,
                    ack_record_id,
                    str(detail) if detail is not None else None,
                    status_code,
                    (
                        DeliveryErrorCode.SERVER_RETRY.value
                        if status is DeliveryStatus.RETRY
                        else DeliveryErrorCode.SERVER_REJECTED.value
                        if status is DeliveryStatus.REJECTED
                        else None
                    ),
                )
            return DeliveryResult(
                DeliveryStatus.RETRY,
                ack_record_id,
                f"HTTP {status_code}: {_response_detail(response)}",
                status_code,
                DeliveryErrorCode.HTTP_ERROR.value,
            )
        except Exception as exc:  # noqa: BLE001 - transport isolation boundary
            return DeliveryResult(
                DeliveryStatus.RETRY,
                None,
                str(exc),
                error_code=DeliveryErrorCode.TRANSPORT_ERROR.value,
            )


def _requests_post(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.post(*args, **kwargs)


def _response_detail(response: Any) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - optional response compatibility
        body = getattr(response, "text", "")
    return str(body)
