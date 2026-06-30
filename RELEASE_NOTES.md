# Senior Pomidor Edge Node v0.1.0-alpha

First public release candidate for the Senior Pomidor Raspberry Pi edge-node repository.

## Highlights

- Raspberry Pi and mock-mode edge telemetry collection.
- Two-pod plant telemetry payload using `senior-pomidor.edge.telemetry.v2`.
- MQTT telemetry publishing with optional HTTP fallback.
- Local buffering for telemetry, lifecycle events, and photos.
- USB camera capture with local metadata and optional multipart upload.
- Raspberry Pi setup automation and 24/7 operations runbooks.
- Hardware calibration documentation for soil, temperature, light, leaf temperature, and system health sensors.
- JSON schemas and contract tests for telemetry, event, and photo payloads.
- CI coverage for tests, formatting, linting, typing, dependency audit, Docker config, shell script checks, Dockerfile checks, and secret scanning.

## Known Limitations

- This repository contains the edge-node layer only.
- Core server, database, dashboards, state estimation, AI/VLM processing, and public datasets live outside this runtime scope.
- Real hardware mode is intended for Raspberry Pi Linux.
- Actuation and autonomous control are not included.
- Public research outputs and dataset publication are roadmap items.

## Suggested Release Assets

- Architecture overview.
- Hardware BOM and wiring guide.
- Example telemetry, event, and photo metadata payloads.
- Public status page screenshot.
- Privacy-safe representative hardware or plant photos.
