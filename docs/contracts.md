# Edge Contract Policy

This repository owns the edge-node contracts emitted by the Raspberry Pi or mock edge process. The active contracts are:

- `senior-pomidor.edge.telemetry.v2`
- `senior-pomidor.edge.event.v1`
- `senior-pomidor.edge.photo.v1`

Machine-readable schemas live in `schemas/`. Reusable valid examples live in `tests/fixtures/`.

## Versioning

`schema_version` is required on every payload. Consumers must dispatch by this value, not by topic or endpoint alone.

Compatible changes may add optional fields, add metric names, add error metadata, or add new pod keys. Incompatible changes that remove required fields, rename fields, change units, or change timestamp semantics require a new schema version.

Core consumers must ignore unknown object fields. Edge producers should not reuse an existing field name with a different meaning.

## Timestamps

All timestamps are UTC strings with second precision and a trailing `Z`.

- Telemetry uses `timestamp_utc`.
- Lifecycle events use `timestamp_utc`.
- Photo metadata uses `captured_at_utc` and `uploaded_at_utc`.

Naive timestamps created inside this repo are treated as UTC before formatting.

## Telemetry v2

Telemetry payloads are published once per collection tick with schema version `senior-pomidor.edge.telemetry.v2`.

Required top-level fields:

- `schema_version`
- `device_id`
- `timestamp_utc`
- `pods`
- `system_health`

`pods` currently includes `pod_1` and `pod_2`. Each pod has:

- `enabled`: boolean connection/configuration state.
- `metrics`: numeric metric map.
- `errors`: array of non-fatal sensor errors with `sensor` and `message`.

Shared air, light, and leaf-temperature readings are merged into each enabled pod. If a shared sensor fails, its error appears in each enabled pod because that pod is missing the shared measurement for that tick. Disabled pods are still present with `enabled=false`, empty metrics, and no errors.

`system_health` includes `rpi_core`, `pod_1_hardware`, and `errors`. Health metrics are numeric or boolean. Failed health probes omit their metric values and add entries to `system_health.errors`.

Common metric units:

| Suffix/name | Unit |
| --- | --- |
| `_c` | degrees Celsius |
| `_percent` | percent |
| `_hpa` | hectopascals |
| `_lux` | lux |
| `_kpa` | kilopascals |
| `_v` | volts |
| `_ma` | milliamps |
| `_dbm` | dBm |
| `_bytes` | bytes |
| `_count` | count |
| `adc_raw` | ADS1115 raw reading |

VPD fields are derived on the edge node when the required raw measurements are available. Missing inputs omit only the derived fields they affect.

## Event v1

Lifecycle event payloads use schema version `senior-pomidor.edge.event.v1`.

Required fields:

- `schema_version`
- `event_id`
- `device_id`
- `event_type`
- `timestamp_utc`
- `source`

Supported `event_type` values are `maintenance_started` and `maintenance_completed`. `reason` is optional and intended for operator notes such as `sensor service`.

`event_id` is the idempotency key for event consumers.

## Photo v1

Photo metadata sidecars use schema version `senior-pomidor.edge.photo.v1`. The JPEG bytes are not embedded in telemetry.

Required fields:

- `schema_version`
- `photo_id`
- `device_id`
- `captured_at_utc`
- `file_name`
- `file_size_bytes`
- `sharpness_score`
- `attempts`
- `upload_status`
- `uploaded_at_utc`

`upload_status` is `pending` until a multipart upload receives a 2xx response, then it becomes `uploaded` and `uploaded_at_utc` is set. `photo_id` is the idempotency key for photo consumers.
