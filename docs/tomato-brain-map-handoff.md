# Tomato Brain Map: Edge producer handoff

Status: accepted preparation baseline, 2026-09-09; conformance execution NOT_RUN.
Owner: Edge producer maintainer. Consumer: Server Map adapter.
Authority: [cross-system decisions](https://github.com/cracketus/senior-pomidor/blob/main/docs/architecture/tomato-brain-map/implementation-decisions.md).
Server owns [Map specification](https://github.com/cracketus/senior-pomidor-server/blob/docs/tomato-brain-map-prep/docs/TOMATO_BRAIN_MAP_R1_SPEC.md).

## Current compatibility baseline

Inspected main bd202f51122c1ef81c74edff3e349b3ced974666:
[telemetry schema](../schemas/edge-telemetry-v2.schema.json),
[Core integration](core-integration.md),
[spool](../src/telemetry_spool.py),
[edge health](../src/edge_health.py).
R1 consumes the existing producer contract. No new required payload field, firmware change, hardware discovery or production deployment is needed for synthetic Map work.

HTTP acknowledgement matching the durable record identity owns spool completion; MQTT is an optional mirror. A received MQTT record does not prove HTTP delivery completion. Native aggregate Edge status remains a source report, not a per-probe diagnosis.
No stable physical asset/target/calibration history is inferred from a pod key. Logical source identity is device namespace plus channel. Real mappings require evidence; synthetic fixtures use explicit A/B mappings and synthetic calibration.

## Producer/consumer rules

| Producer evidence | Consumer obligation |
| --- | --- |
| Original observation timestamp | Preserve through queue, retry and replay; Core receipt time is separate |
| record_id | Same observation across retries/transports; Core deduplicates |
| Enabled channel with percentage | Finite 0..100, explicit configured provenance required for usable soil capability |
| ADC only | Raw evidence; never silently converted to percentage |
| Explicit channel read error or invalid latest value | Invalidate that channel; earlier value is historical context only |
| Omitted channel without explicit failure | Missing update; prior valid observation ages normally |
| Edge health/backlog | Keep native enum and report timestamp; do not infer physical cable failure |
| Unsupported error semantics or unknown binding | UNKNOWN, with a reason; no guessed attribution |

An error is channel-scoped only when stored producer fields unambiguously identify that sensor/channel. Unscoped errors remain source-level evidence. Air failure must not invalidate soil by string matching. The implementation conformance task must enumerate actual producer error forms before adapter mapping.

## Synthetic conformance corpus to implement

All identities, times and calibration references are synthetic. Keep original fixtures; add cases without modifying the accepted wire contract.

| Case | Producer path and expected consumer result |
| --- | --- |
| E01 normal A/B | Formatter -> spool -> HTTP -> Core persistence -> Map sees two independent targets |
| E02 retry/restart | Stable record/time after restart; no duplicate observation |
| E03 delayed/backlog | Measurement at 10:00, receipt at 13:00; different Map modes produce different histories |
| E04 MQTT success, HTTP failure | Core may have observation while spool remains pending |
| E05 fresh air, omitted soil | Moisture freshness not reset |
| E06 explicit soil failure | Only identified channel invalidated; physical cause unknown |
| E07 invalid/ADC-only soil | No calibrated percentage capability |
| E08 future time | UNKNOWN clock quality, no rewrite of source timestamp |
| E09 provenance absent | Synthetic explicit config can supply provenance; real unknown provenance cannot turn green |
| E10 source disappears | Fresh Core observation expires; Pi/network/physical acquisition cause unknown |

Map E01-E10 to existing [temporal tests #131](https://github.com/cracketus/senior-pomidor-plant-v2/issues/131), Server #252/#248 and umbrella #107-#111. The new Map-specific task owns adapter compatibility evidence only; do not duplicate generic spool hardening.
Future calibration #70, topology manifest #71 and observation linkage #72 remain separately scoped. Local diagnostics #108-#111 is a later independent source, not a required R1 transport.

## Acceptance and rollout

Done means machine-replayable synthetic fixtures pass through actual producer and isolated Core consumer boundaries, with pinned producer/consumer SHAs, expected results and a bounded report. Schema-only validation is insufficient.
Run the CONTRIBUTING checks for executable changes: python -m pytest -q; ruff format --check .; ruff check .; mypy src. Exercise fake sensors and isolated storage; no GPIO, external export or live private data.
Mismatch handling: preserve existing production payloads, fix the additive Map adapter or propose a separately versioned contract change with rollout order. Never silently rename producer fields to satisfy a fixture.
There is no Edge runtime rollout in this preparation. If later compatibility code changes are needed, ship the compatible Server reader first, then isolated Edge conformance, then separately authorized canary. Rollback restores the prior producer without deleting pending spool data.

## Real-data activation checklist

Owner: project operator with Edge maintainer. These are activation evidence gaps, not architecture questions:
- [ ] Verified device/channel -> target mapping with effective and recorded timestamps.
- [ ] Calibration/conversion provenance for each claimed percentage.
- [ ] Known supported producer schema/error mapping.
- [ ] Sanitized evidence proving observation and receipt timing.
Until supplied, use only synthetic claims and explicit UNKNOWN for missing real mappings. Hardware condition and calibration accuracy remain unverified by CI.

Owning task: [Edge #150](https://github.com/cracketus/senior-pomidor-plant-v2/issues/150). Programme acceptance: [umbrella #120](https://github.com/cracketus/senior-pomidor/issues/120).
