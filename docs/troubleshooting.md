# Edge troubleshooting

This runbook is the first place to look when the Raspberry Pi edge node shows a
yellow or red indicator, telemetry delivery is delayed, or the container logs
transport errors.

## Quick triage

Run these commands from the repository root on the edge host:

```bash
docker compose ps
docker compose logs --since 15m senior-pomidor-edge
docker compose exec senior-pomidor-edge python scripts/telemetry_spool.py status
```

The spool status is the local source of truth for delivery. Check:

- `status`, `pending_count`, `in_flight_count`, and `backlog_count` for current queue state;
- `dead_letter_count` for records that will not be retried automatically;
- `last_error_code` and `last_error_detail` for the most recent failed attempt;
- `last_successful_delivery_at_utc`, which should advance after HTTP acknowledgement;
- `worker_state` (`running`) and `worker_last_error`;
- `disk_status` and `free_space_bytes` for storage pressure.

Inspect the latest telemetry payload too. `system_health.aggregate.reasons`
explains the canonical indicator state, while `system_health.indicator` reports
the requested and rendered LED state.

## Indicator meanings

| LED pattern | State | Meaning |
|---|---|---|
| Green steady | `OK` | Required health probes pass and the spool has no pending records. |
| Green pulsing | `STARTUP` | The collector has not completed its first health evaluation. |
| Yellow blinking | `BACKLOG` | Records are pending/in flight, HTTP delivery is unavailable, or disk use is at the warning threshold. |
| Yellow steady | `DEGRADED` | A health probe, application, watchdog, power, storage, or data-integrity condition is degraded. A non-empty `dead_letter` set also makes the spool degraded. |
| Red + yellow steady | `MAINTENANCE` | Watchdog maintenance hold is active. |
| Red blinking | `CRITICAL` | A critical storage, filesystem, power, or watchdog condition is active. |

The indicator is updated after each telemetry cycle. Allow at least one
`POLL_INTERVAL_SECONDS` interval after recovery. If the local spool says `OK`
but the LED still shows yellow, inspect `system_health.indicator` and then
restart the container only after recording the values:

```bash
docker compose restart senior-pomidor-edge
```

## Backlog and delivery

Telemetry is committed to SQLite before network delivery. HTTP is the
authoritative transport: a record is delivered only when Core returns an
acknowledgement with the same `record_id` and status `accepted` or `duplicate`.
MQTT is a best-effort mirror; a successful MQTT publish does not drain the
spool.

When `pending_count` is greater than zero, follow the queue and worker log:

```bash
docker compose logs -f senior-pomidor-edge
docker compose exec senior-pomidor-edge python scripts/telemetry_spool.py status
```

The queue is recovering when `pending_count` decreases and
`last_successful_delivery_at_utc` advances. If it does not decrease, inspect
one record and its complete attempt history:

```bash
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py show RECORD_ID
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py history --record-id RECORD_ID --sort newest
```

Common delivery errors:

- `transport_error`: DNS, routing, connection, or timeout failure.
- `http_error`: Core returned an HTTP error status.
- `server_rejected`: Core rejected the payload; inspect Core response detail and server logs.
- `server_retry`: Core asked the edge to retry later.
- `invalid_response` or `invalid_ack_status`: Core did not return expected acknowledgement JSON.
- `ack_record_id_mismatch`: the acknowledgement omitted or returned a different `record_id`.
- `retry_exhausted`: the preceding failure repeated until the retry limit was reached.

## MQTT connection refused

For `MQTT telemetry failed: [Errno 111] Connection refused`, verify the
configured endpoint and broker listener:

```bash
grep -E '^(MQTT_HOST|MQTT_PORT|MQTT_TLS)=' .env
nc -vz MQTT_HOST MQTT_PORT
```

On the broker host:

```bash
sudo ss -ltnp | grep 1883
sudo systemctl status mosquitto
sudo journalctl -u mosquitto --since "30 minutes ago"
```

`MQTT_TLS=false` normally pairs with port `1883`. If credentials are empty,
the broker must intentionally allow anonymous access; otherwise configure
`MQTT_USERNAME` and `MQTT_PASSWORD`. Do not expose credentials in issue
reports or log excerpts.

MQTT failure is independent from the authoritative HTTP queue. Also verify
the Core endpoint:

```bash
nc -vz CORE_HOST 8000
```

The telemetry fields `network.mqtt_broker_reachable` and
`network.http_telemetry_reachable` identify which path is unreachable.

## Dead-letter records

Dead-letter records are not retried automatically and are intentionally not
deleted by the CLI. First inspect and archive them:

```bash
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py list --state dead_letter
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py export /app/data/dead-letter-archive.jsonl --state dead_letter
```

If the delivery problem is fixed and the record should be sent, return it to
the queue:

```bash
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py retry-dead RECORD_ID
```

Use `resolve-dead` only for an exact `legacy-*` record proven to already exist
on the server. It records immutable evidence, does not send the payload again,
and changes the logical state to `reconciled`:

```bash
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py resolve-dead LEGACY_RECORD_ID \
  --reason already_present_on_server \
  --evidence 'server telemetry_events.id=SERVER_ID'
```

Never resolve a record from payload similarity or assumption alone. Keep a
backup before operational database changes and verify `dead_letter_count=0`
after reconciliation.

## Storage and database checks

If `disk_status` is `DEGRADED` or `CRITICAL`, free space outside the spool
first. Do not manually delete the SQLite database, WAL/SHM files, pending
records, or dead-letter records.

```bash
df -h /
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py integrity-check
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py online-backup /app/data/backups/telemetry-spool.sqlite3
```

The cleanup command removes only eligible old delivered data:

```bash
docker compose exec senior-pomidor-edge \
  python scripts/telemetry_spool.py cleanup --delivered-retention-days 7
```

For corruption, disk exhaustion, schema migration, and rollback procedures,
continue with [the telemetry spool runbook](telemetry-spool-runbook.md).

## Hardware and GPIO indicator

If the payload says `aggregate.state=OK` and the indicator requests/renders
`OK`, but the physical LED is still yellow, check the GPIO mapping and wiring:
red `17`, yellow `27`, green `22` by default. Run the indicator smoke test
during a maintenance window:

```bash
python scripts/health_indicator_smoke.py --real-gpio
```

Review the board isolation, common ground, and series resistors in
[edge-health-indicator.md](edge-health-indicator.md) before changing pins or
enabling the GPIO backend.
