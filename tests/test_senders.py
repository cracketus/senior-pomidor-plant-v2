from src.config import load_config
from src.network.http_sender import HttpSender
from src.network.mqtt_sender import MqttSender


def test_mqtt_sender_returns_false_on_client_failure() -> None:
    settings = load_config({"MQTT_HOST": "core.local"})
    sender = MqttSender(settings, client_factory=lambda: FailingMqttClient())

    assert sender.publish({"hello": "world"}) is False


def test_http_sender_disabled_returns_false() -> None:
    settings = load_config({"MQTT_HOST": "core.local", "HTTP_ENABLED": "false"})

    assert HttpSender(settings).send({"hello": "world"}) is False


class FailingMqttClient:
    def connect(self, *_args, **_kwargs):
        raise OSError("network unavailable")
