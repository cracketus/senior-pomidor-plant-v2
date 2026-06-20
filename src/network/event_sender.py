"""MQTT lifecycle event sender."""

from __future__ import annotations

import json
import logging
import ssl
from collections.abc import Callable
from typing import Any

from src.config import Settings, mqtt_event_topic


class MqttEventSender:
    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self.client_factory = client_factory

    def publish(self, event: dict[str, Any]) -> bool:
        topic = mqtt_event_topic(self.settings)
        body = json.dumps(event, separators=(",", ":"), sort_keys=True)

        try:
            client = self.client_factory() if self.client_factory else self._create_client()
            if self.settings.mqtt_username:
                client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)
            if self.settings.mqtt_tls:
                client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
            client.connect(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
            info = client.publish(topic, body, qos=1, retain=False)
            if hasattr(info, "wait_for_publish"):
                info.wait_for_publish(timeout=10)
            client.disconnect()
            self.logger.info("MQTT lifecycle event published to %s", topic)
            return True
        except Exception as exc:  # noqa: BLE001 - transport isolation boundary
            self.logger.error("MQTT lifecycle event failed: %s", exc)
            return False

    def _create_client(self) -> Any:
        import paho.mqtt.client as mqtt

        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
