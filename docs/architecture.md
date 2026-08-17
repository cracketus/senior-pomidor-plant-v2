# Edge Architecture

Senior Pomidor Edge Node is the Raspberry Pi layer of the project. It reads local sensors, stores observations locally, captures optional photos, and publishes contract-shaped payloads to the Core server.

```mermaid
flowchart LR
    sensors[Plant and system sensors] --> edge[Edge node runtime]
    camera[USB camera] --> edge
    edge --> storage[SQLite telemetry spool, event and photo buffers]
    edge --> mqtt[MQTT telemetry and events]
    edge --> http[Required acknowledged telemetry HTTP and optional photo upload]
    mqtt --> core[Core server outside this repo]
    http --> core
```

## Runtime Boundaries

- In scope: sensor reads, derived VPD metrics, durable telemetry spooling, lifecycle events, photo capture, payload formatting, MQTT mirroring, acknowledged HTTP delivery, and Raspberry Pi setup automation.
- Out of scope: Core server, database, dashboards, AI/VLM processing, runtime state estimation, public datasets, actuation, and autonomous control.

## Failure Model

Sensor and health probes are isolated. A failed probe reports an error field and should not stop the main telemetry loop. Telemetry is committed to the WAL-backed SQLite spool before network delivery. MQTT never acknowledges durable delivery; the HTTP application acknowledgement does. See [the spool runbook](telemetry-spool-runbook.md).
