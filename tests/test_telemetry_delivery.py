import pytest

from src.config import ConfigError, load_config, public_settings
from src.network.http_sender import HttpSender
from src.telemetry_spool import DeliveryStatus


def valid_env(**overrides: str) -> dict[str, str]:
    env = {
        "MQTT_HOST": "core.local",
        "HTTP_ENABLED": "true",
        "CORE_HTTP_URL": "https://core.example/telemetry",
        "MOCK_SENSORS": "true",
    }
    env.update(overrides)
    return env


@pytest.mark.parametrize(
    "env",
    [
        {"MQTT_HOST": "core.local", "CORE_HTTP_URL": "https://core.example/telemetry"},
        {"MQTT_HOST": "core.local", "HTTP_ENABLED": "true"},
        {
            "MQTT_HOST": "core.local",
            "HTTP_ENABLED": "false",
            "CORE_HTTP_URL": "https://core.example/telemetry",
        },
    ],
)
def test_http_telemetry_configuration_is_mandatory(env) -> None:
    with pytest.raises(ConfigError, match="HTTP_ENABLED=true and CORE_HTTP_URL"):
        load_config(env)


def test_spool_configuration_and_token_secrecy() -> None:
    settings = load_config(
        valid_env(
            TELEMETRY_UPLOAD_TOKEN="secret",
            TELEMETRY_SPOOL_RETRY_SCHEDULE_SECONDS="1,2,9",
            TELEMETRY_SPOOL_MAX_ATTEMPTS="8",
        )
    )

    assert settings.telemetry_spool_retry_schedule_seconds == (1, 2, 9)
    assert settings.telemetry_spool_max_attempts == 8
    assert settings.telemetry_upload_token == "secret"
    assert "secret" not in repr(public_settings(settings))


def test_retention_payload_and_capacity_are_validated() -> None:
    with pytest.raises(ConfigError, match="TELEMETRY_SPOOL_PENDING_RETENTION_DAYS must be >= 14"):
        load_config(valid_env(TELEMETRY_SPOOL_PENDING_RETENTION_DAYS="13"))

    with pytest.raises(ConfigError, match="TELEMETRY_SPOOL_CAPACITY_MB is too small"):
        load_config(valid_env(TELEMETRY_SPOOL_CAPACITY_MB="1"))

    settings = load_config(
        valid_env(
            POLL_INTERVAL_SECONDS="3600",
            TELEMETRY_SPOOL_PENDING_RETENTION_DAYS="14",
            TELEMETRY_SPOOL_DELIVERED_RETENTION_DAYS="1",
            TELEMETRY_SPOOL_MAX_PAYLOAD_BYTES="1024",
            TELEMETRY_SPOOL_CAPACITY_MB="1",
            TELEMETRY_SPOOL_DEAD_LETTER_RETENTION_DAYS="999",
        )
    )
    assert settings.telemetry_spool_pending_retention_days == 14
    assert settings.telemetry_spool_max_payload_bytes == 1024
    assert not hasattr(settings, "telemetry_spool_dead_letter_retention_days")


class Response:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body
        self.text = "response"

    def json(self):
        return self._body


def test_http_sender_requires_matching_application_ack_and_uses_bearer_token() -> None:
    settings = load_config(valid_env(TELEMETRY_UPLOAD_TOKEN="secret"))
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response(202, {"record_id": "record-1", "status": "accepted"})

    result = HttpSender(settings, post_func=post).send({"record_id": "record-1"})

    assert result.status is DeliveryStatus.ACCEPTED
    assert captured["headers"] == {"Authorization": "Bearer secret"}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"record_id": "wrong", "status": "accepted"}, DeliveryStatus.RETRY),
        ({"record_id": "record-1", "status": "duplicate"}, DeliveryStatus.DUPLICATE),
        ({"record_id": "record-1", "status": "rejected"}, DeliveryStatus.REJECTED),
        ({"record_id": "record-1", "status": "unknown"}, DeliveryStatus.RETRY),
        ("invalid", DeliveryStatus.RETRY),
    ],
)
def test_http_sender_ack_contract(body, expected) -> None:
    settings = load_config(valid_env())
    sender = HttpSender(settings, post_func=lambda *_args, **_kwargs: Response(202, body))

    assert sender.send({"record_id": "record-1"}).status is expected


def test_auth_failure_is_retryable() -> None:
    settings = load_config(valid_env())
    sender = HttpSender(
        settings,
        post_func=lambda *_args, **_kwargs: Response(401, {"record_id": "record-1", "status": "retry"}),
    )

    assert sender.send({"record_id": "record-1"}).status is DeliveryStatus.RETRY


@pytest.mark.parametrize("status_code", [401, 429, 500, 503])
def test_rejected_ack_with_http_error_is_retryable(status_code) -> None:
    settings = load_config(valid_env())
    sender = HttpSender(
        settings,
        post_func=lambda *_args, **_kwargs: Response(
            status_code,
            {"record_id": "record-1", "status": "rejected"},
        ),
    )

    assert sender.send({"record_id": "record-1"}).status is DeliveryStatus.RETRY
