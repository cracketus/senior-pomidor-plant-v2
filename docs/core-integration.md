# Core Integration

This document describes how the edge node sends its active contracts to Core. It does not define Core storage, AI, dashboards, or actuation behavior.

## MQTT Telemetry Mirror

MQTT is an optional best-effort mirror. A QoS 1 PUBACK never marks a spool record delivered.

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
- A completed publish is diagnostic only; HTTP application acknowledgement remains authoritative.

Core should validate `schema_version`, accept duplicate payloads safely, and ignore unknown fields.

## MQTT Lifecycle Events

Planned maintenance events publish to:

```text
{MQTT_TOPIC_PREFIX}/{DEVICE_ID}/events
```

Payload: JSON object with `schema_version=senior-pomidor.edge.event.v1`.

The MQTT auth, TLS, QoS, and retain behavior matches telemetry. Core should treat `event_id` as the idempotency key.

## HTTP Telemetry Delivery

`HTTP_ENABLED=true` and `CORE_HTTP_URL` are mandatory. The delivery worker posts every spooled record even when its MQTT mirror succeeded.

Request behavior:

- Method: `POST`
- Body: JSON telemetry payload
- Timeout: `HTTP_TIMEOUT_SECONDS`
- Optional header: `Authorization: Bearer <TELEMETRY_UPLOAD_TOKEN>`
- Acknowledgement: JSON containing the same `record_id` and one of `accepted`, `duplicate`, `retry`, or `rejected`

Only `accepted` and `duplicate` mark delivery complete. `rejected` creates a dead letter. Missing, malformed, or mismatched acknowledgements and network, timeout, authentication, 429, or 5xx failures remain retryable.

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

Telemetry is committed to `TELEMETRY_SPOOL_DB_PATH` before either transport runs. A separate worker selects one newest eligible record plus the oldest backlog records in a bounded batch, preserving live priority while draining an outage. Retry state and the complete attempt history survive restarts.

Legacy JSON under `LOCAL_STORAGE_DIR` is imported oldest-first once SQLite commits it. Invalid files remain for inspection.

Lifecycle events are saved before publish. The maintenance command replays up to 10 pending events oldest-first before sending the current event. Corrupt queued files are skipped and left in place for operator inspection.

Photo JPEGs and metadata sidecars are saved locally before upload. Pending photos are uploaded oldest-first during camera upload cycles.

Telemetry cleanup deletes only delivered records older than the configured retention. Pending and dead-letter data is never automatically deleted. Photo storage keeps its existing file cleanup behavior.

## Consumer Expectations

Core consumers should:

- Dispatch by `schema_version`.
- Accept UTC timestamps with trailing `Z`.
- Ignore unknown fields.
- Preserve raw payloads or enough metadata for diagnostics.
- Treat `record_id`, `event_id`, and `photo_id` as idempotency keys.
- Return the acknowledgement only after durable telemetry acceptance.
- Evaluate `systemd_*` only when `system_health.application.systemd_service_name` is present.
- Treat `system_health.watchdog.configured=false` as intentionally unsupervised and neutral.
- Treat a configured fresh `healthy` watchdog as healthy, and `watchdog.state=unavailable` as degraded.
- Use telemetry timestamp freshness for collector loss; use watchdog status freshness for canonical Docker supervisor health.
- Render Grafana node state from `system_health.aggregate.state` and its machine-readable `reasons`, without synthesizing `systemd_available=false` when the field is absent.

Core consumers should not assume the edge node performs state estimation beyond VPD metrics, weather enrichment, actuation decisions, anomaly classification, dashboard storage, or AI/VLM analysis.

## Compatibility Check

Run `python scripts/compatibility_check.py --env-file .env` on the Raspberry Pi to verify the active Core integration.

The command:

- publishes one MQTT telemetry payload
- posts the same telemetry payload to `CORE_HTTP_URL`
- uploads one generated JPEG to `PHOTO_UPLOAD_URL`
- performs GET checks against telemetry and photo metadata read APIs
- prints pass/fail status for every step and exits non-zero on any failure

Set read API URLs with command flags or environment variables:

```bash
CORE_TELEMETRY_READ_URL='http://core.local:8000/api/v1/edge/telemetry?device_id=balcony-edge-01'
PHOTO_METADATA_READ_URL='http://core.local:8000/api/v1/edge/photos?device_id=balcony-edge-01'
```

If read URLs are omitted, the command appends query parameters to `CORE_HTTP_URL` and `PHOTO_UPLOAD_URL`.
