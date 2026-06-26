# Core Integration

This document describes how the edge node sends its active contracts to Core. It does not define Core storage, AI, dashboards, or actuation behavior.

## MQTT Telemetry

Telemetry is the primary transport.

Topic:

```text
{MQTT_TOPIC_PREFIX}/{DEVICE_ID}/telemetry
```

Payload: JSON object with `schema_version=senior-pomidor.edge.telemetry.v2`.

MQTT behavior:

- QoS is `1`.
- Retain is `false`.
- Username/password are used when `MQTT_USERNAME` is set.
- TLS is enabled when `MQTT_TLS=true` and uses certificate-required validation from the local TLS stack.
- A publish is considered successful when the client call completes without raising.

Core should validate `schema_version`, accept duplicate payloads safely, and ignore unknown fields.

## MQTT Lifecycle Events

Planned maintenance events publish to:

```text
{MQTT_TOPIC_PREFIX}/{DEVICE_ID}/events
```

Payload: JSON object with `schema_version=senior-pomidor.edge.event.v1`.

The MQTT auth, TLS, QoS, and retain behavior matches telemetry. Core should treat `event_id` as the idempotency key.

## HTTP Telemetry Fallback

When MQTT telemetry delivery fails and `HTTP_ENABLED=true`, the edge node posts the same telemetry JSON to `CORE_HTTP_URL`.

Request behavior:

- Method: `POST`
- Body: JSON telemetry payload
- Timeout: `HTTP_TIMEOUT_SECONDS`
- Success: any response accepted by `requests.raise_for_status()`

HTTP fallback is not attempted when MQTT succeeds. Failed fallback leaves the local telemetry file queued for later replay.

## Photo Upload

Photos are uploaded over HTTP multipart when `PHOTO_UPLOAD_ENABLED=true`.

Endpoint: `PHOTO_UPLOAD_URL`

Request:

- Method: `POST`
- File field: `photo`
- File content type: `image/jpeg`
- Form fields: `photo_id`, `device_id`, `captured_at_utc`, `schema_version`, `sharpness_score`
- Optional header: `Authorization: Bearer <PHOTO_UPLOAD_TOKEN>`
- Timeout: `HTTP_TIMEOUT_SECONDS`

Core should treat `photo_id` as the idempotency key and return any 2xx status after accepting the upload. On non-2xx responses or transport errors, the edge keeps the photo metadata in `pending` state for retry.

## Local Buffering And Replay

Telemetry is saved locally before network delivery. On successful delivery, the queued file is deleted. On failure, it remains under `LOCAL_STORAGE_DIR`.

At the start of each telemetry loop, the edge replays up to 10 pending telemetry files oldest-first. Replay stops at the first failed delivery so ordering is preserved for the remaining queue.

Lifecycle events are saved before publish. The maintenance command replays up to 10 pending events oldest-first before sending the current event. Corrupt queued files are skipped and left in place for operator inspection.

Photo JPEGs and metadata sidecars are saved locally before upload. Pending photos are uploaded oldest-first during camera upload cycles.

Telemetry and photo storage cleanup use `LOCAL_STORAGE_MAX_AGE_DAYS` and `LOCAL_STORAGE_MAX_SIZE_MB`.

## Consumer Expectations

Core consumers should:

- Dispatch by `schema_version`.
- Accept UTC timestamps with trailing `Z`.
- Ignore unknown fields.
- Preserve raw payloads or enough metadata for diagnostics.
- Treat `event_id` and `photo_id` as idempotency keys.
- Return 2xx only after durable acceptance of HTTP telemetry or photo uploads.

Core consumers should not assume the edge node performs state estimation beyond VPD metrics, weather enrichment, actuation decisions, anomaly classification, dashboard storage, or AI/VLM analysis.
