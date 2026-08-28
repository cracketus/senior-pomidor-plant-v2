"""Assert one edge telemetry record is delivered over MQTT and HTTP."""

from __future__ import annotations

import json
import math
import os
import queue
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from jsonschema import Draft202012Validator

READY_FILE = Path("/tmp/integration-subscriber-ready")
SCHEMA_PATH = Path("/schemas/edge-telemetry-v2.schema.json")


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def wait_for_http_payload(url: str, record_id: str, deadline: float) -> dict[str, Any]:
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = json.load(response)
            for payload in body.get("payloads", []):
                if payload.get("record_id") == record_id:
                    return payload
            last_error = f"record {record_id} not present"
        except (OSError, ValueError, TypeError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for HTTP payload: {last_error}")


def wait_for_delivered(db_path: str, record_id: str, deadline: float) -> None:
    last_state = "database not available"
    while time.monotonic() < deadline:
        try:
            uri = f"file:{Path(db_path).as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1) as connection:
                row = connection.execute("SELECT state FROM records WHERE record_id=?", (record_id,)).fetchone()
            last_state = str(row[0]) if row else "record not present"
            if last_state == "delivered":
                return
        except sqlite3.Error as exc:
            last_state = str(exc)
        time.sleep(0.2)
    raise AssertionError(f"record {record_id} did not become delivered; last state: {last_state}")


def assert_mock_values(payload: dict[str, Any]) -> None:
    pod_1 = payload["pods"]["pod_1"]["metrics"]
    pod_2 = payload["pods"]["pod_2"]["metrics"]
    expected_pod_1 = {
        "adc_raw": 12500.0,
        "soil_temperature_c": 22.1,
        "air_temperature_c": 24.0,
        "air_humidity_percent": 58.0,
        "air_pressure_hpa": 1008.5,
        "light_lux": 18500.0,
        "ir_ambient_temp_c": 23.7,
        "leaf_temp_c": 24.9,
    }
    expected_pod_2 = {**expected_pod_1, "adc_raw": 11800.0, "soil_temperature_c": 22.4}
    for name, expected in expected_pod_1.items():
        actual = pod_1.get(name)
        assert isinstance(actual, int | float) and math.isclose(actual, expected, abs_tol=1e-9), (
            f"pod_1 {name}: expected {expected}, got {actual}"
        )
    for name, expected in expected_pod_2.items():
        actual = pod_2.get(name)
        assert isinstance(actual, int | float) and math.isclose(actual, expected, abs_tol=1e-9), (
            f"pod_2 {name}: expected {expected}, got {actual}"
        )


def main() -> None:
    device_id = required_env("DEVICE_ID")
    expected_topic = required_env("EXPECTED_TOPIC")
    mqtt_host = required_env("MQTT_HOST")
    mqtt_port = int(required_env("MQTT_PORT"))
    timeout = float(required_env("TEST_TIMEOUT_SECONDS"))
    deadline = time.monotonic() + timeout
    messages: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
    subscribed = False

    def on_message(
        _client: mqtt.Client,
        _userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        payload = json.loads(message.payload.decode("utf-8"))
        messages.put((message.topic, payload))

    def on_subscribe(
        client: mqtt.Client,
        _userdata: Any,
        _mid: int,
        _reason_codes: list[mqtt.ReasonCode],
        _properties: mqtt.Properties | None,
    ) -> None:
        nonlocal subscribed
        marker = client.publish("integration/readiness", "subscriber-ready", qos=0, retain=True)
        if marker.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"failed to publish readiness marker: MQTT error {marker.rc}")
        READY_FILE.touch()
        subscribed = True
        print(f"integration-test: subscribed to {expected_topic}", flush=True)

    def on_connect(
        client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            raise RuntimeError(f"MQTT connection failed: {reason_code}")
        client.subscribe(expected_topic, qos=1)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.connect(mqtt_host, mqtt_port, keepalive=15)
    client.loop_start()
    try:
        while not subscribed and time.monotonic() < deadline:
            time.sleep(0.05)
        if not subscribed:
            raise AssertionError("timed out waiting for MQTT subscription acknowledgement")

        remaining = max(0.1, deadline - time.monotonic())
        try:
            topic, mqtt_payload = messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise AssertionError("timed out waiting for MQTT telemetry") from exc
        assert topic == expected_topic, f"expected topic {expected_topic}, got {topic}"
        record_id = mqtt_payload.get("record_id")
        assert isinstance(record_id, str) and record_id, "MQTT payload has no record_id"

        http_payload = wait_for_http_payload(required_env("CORE_RECEIVED_URL"), record_id, deadline)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        validator.validate(mqtt_payload)
        validator.validate(http_payload)
        assert mqtt_payload["device_id"] == device_id
        assert http_payload["device_id"] == device_id
        assert_mock_values(mqtt_payload)
        assert mqtt_payload == http_payload, "MQTT and HTTP payloads differ"
        wait_for_delivered(required_env("SPOOL_DB_PATH"), record_id, deadline)
        print(f"integration-test: record {record_id} delivered over MQTT and HTTP", flush=True)
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
