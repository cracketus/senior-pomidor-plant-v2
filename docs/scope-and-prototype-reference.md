# Scope And Prototype Reference

This repository is the Senior Pomidor edge-node implementation. It owns hardware collection, local buffering, payload formatting, and transport from the balcony device to Core.

## Owned Here

- Raspberry Pi and mock sensor collection.
- Two-pod edge telemetry formatting.
- Edge-derived VPD metrics.
- MQTT telemetry publish.
- Optional HTTP telemetry fallback.
- Planned maintenance lifecycle events.
- USB camera capture, validation, local metadata, and HTTP multipart upload.
- Local telemetry, event, and photo buffering.
- Raspberry Pi setup and operations runbooks.

## Owned By Core Or Future Repositories

- Long-lived database models.
- Dashboards and user-facing APIs.
- State estimation beyond edge VPD fields.
- Weather adapters and forecast ingestion.
- Anomaly classifiers.
- Action planning and actuation.
- Guardrails and safety policy engines.
- LLM/VLM prompts, model routing, and analysis pipelines.

## Prototype Material

Prototype contracts such as `state_v1`, `action_v1`, and `anomaly_v1`, plus WeatherAdapter, Guardrails, Control, LLM, and VLM specifications, are reference-only for this repo. They are not active runtime behavior and should not be treated as Core contract commitments from the edge node.

Migrating prototype material into this repository requires a dedicated contract change that names the owned behavior, updates docs and schemas, and adds tests for the edge-node responsibility being introduced.
