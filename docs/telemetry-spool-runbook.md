# Telemetry spool operator runbook

Telemetry is committed to SQLite before any MQTT or HTTP attempt. HTTP is the authoritative transport: only an application acknowledgement containing the same `record_id` and status `accepted` or `duplicate` marks a record delivered. MQTT is a best-effort mirror.

## Location and sizing

The default database is `data/telemetry-spool.sqlite3`; Docker persists it through the existing `./data:/app/data` mount. For a native production installation, use `TELEMETRY_SPOOL_DB_PATH=/var/lib/senior-pomidor-edge/telemetry-spool.sqlite3` on a persistent filesystem. The default capacity is 1536 MB. Delivered rows are retained for seven days. Pending and dead-letter rows are never automatically deleted.

Startup rejects a capacity that cannot hold the configured pending-retention guarantee (30 days by default, never less than 14 days), delivered retention, maximum payload size, and a 25 percent SQLite/service reserve. The sizing inputs are `POLL_INTERVAL_SECONDS`, `TELEMETRY_SPOOL_PENDING_RETENTION_DAYS`, `TELEMETRY_SPOOL_DELIVERED_RETENTION_DAYS`, and `TELEMETRY_SPOOL_MAX_PAYLOAD_BYTES`. Recalculate capacity whenever any of them changes.

SQLite also creates `-wal` and `-shm` siblings. Back up the database with the service stopped, or use SQLite's online backup API. Do not copy only the main file while writes are active.

## Inspection and recovery

Run commands from the repository root (add `--db PATH` when using a non-default location):

```bash
python scripts/telemetry_spool.py status
python scripts/telemetry_spool.py list --state pending --limit 20
python scripts/telemetry_spool.py list --sort oldest
python scripts/telemetry_spool.py show RECORD_ID
python scripts/telemetry_spool.py history --record-id RECORD_ID --sort newest
python scripts/telemetry_spool.py integrity-check
python scripts/telemetry_spool.py checkpoint --truncate
python scripts/telemetry_spool.py retry-dead RECORD_ID
python scripts/telemetry_spool.py list --state reconciled
python scripts/telemetry_spool.py cleanup --delivered-retention-days 7
python scripts/telemetry_spool.py online-backup backups/telemetry-spool.sqlite3
python scripts/telemetry_spool.py export exports/telemetry.jsonl --sort oldest
```

`retry-dead` returns a failed delivery to the network queue. `resolve-dead` is a separate, single-record
operator transition for legacy telemetry that has already been proven present on the server. It never sends the
record again and never deletes its payload or immutable resolution audit. There is deliberately no bulk resolve.

Before resolving one record:

1. Create an online backup and run `integrity-check`.
2. Run `show LEGACY_RECORD_ID` and confirm it is the exact `legacy-*` row in `dead_letter`.
3. Find the matching server `telemetry_events.id` (or an approved stable change-record reference) and retain that
   server-side evidence. Do not resolve from payload similarity or assumption alone.
4. Record delivery/status counters so the post-check can prove that reconciliation did not simulate a network ACK.

Resolve exactly one confirmed record:

```bash
sudo python3 scripts/telemetry_spool.py resolve-dead \
  legacy-aa0a39fda11a78763b19dbd77c3139835401bf7e9b1dde64164ba0e18e35df2e \
  --reason already_present_on_server \
  --evidence 'server telemetry_events.id=<ID>'
```

Then run `show LEGACY_RECORD_ID`, `list --state reconciled`, and `status`. Require logical state
`reconciled`, `dead_letter_count=0`, `reconciled_count=1`, an unchanged set of delivery/success/duplicate/replay
counters, and a running worker. Status is `OK` when the queue is empty or `BACKLOG` while other records drain.
Repeating the exact command is safe and returns `changed=false`; different evidence or reason is rejected.

Rollback requires stopping the edge service and restoring the online backup made immediately before the schema
migration. Do not run an older application against the migrated database: applications that support only schema
v2 fail safe when they see v3. Verify the restored database with `integrity-check`, record/payload counts, and the
pre-change status before restarting the older release.

The CLI intentionally has no command that deletes pending, dead-letter, or reconciled telemetry.

## Corruption

Startup runs `PRAGMA quick_check`. If it fails, the process stops and leaves the database untouched; it never renames or replaces it with an empty database. Stop the service, preserve the database together with its WAL/SHM files, make a forensic copy, then use SQLite recovery tooling or restore a known-good backup.

## Disk exhaustion

At 80/90/95 percent disk use, `disk_status` becomes warning/degraded/critical. Warning triggers delivered cleanup and a WAL checkpoint. Degraded and critical suspend sensor and camera capture without deleting pending or dead-letter data. First free space outside the spool. It is safe to run a checkpoint and delivered cleanup. Never manually delete the database, WAL, pending rows, or dead-letter rows. After storage recovers, collection resumes.

## Schema migration

Opening an older database migrates transactionally to SQLite `user_version=3`. The v1-to-v2 step adds first/last
attempt timestamps, normalized error codes, recovery tracking, and seeds durable totals from existing rows and
delivery history. The v2-to-v3 step adds immutable `dead_letter_resolutions` audit records without rebuilding
`records`. Before deploying, create an online backup. Startup refuses unknown future schema versions and never
replaces the database. Rollback across v3 requires restoring that backup.

## Legacy migration

On startup, valid `data/telemetry/*.json` files are imported oldest-first. Their deterministic IDs make restart safe. A file is removed only after its SQLite transaction commits. Invalid JSON remains in place and makes migration health degraded for operator inspection.

## Raspberry Pi outage rehearsal

1. Create an online backup and record `status` output.
2. Block the HTTP ingestion endpoint while leaving the edge service running for at least two polling intervals.
3. Confirm pending count and oldest age increase, then restart the edge service while one record is `in_flight` if possible.
4. Restore the endpoint and simulate one lost ACK. Confirm the same `record_id` is retried and a server `duplicate` ACK delivers it.
5. Confirm pending and in-flight reach zero, `last_successful_delivery_at_utc` advances, `replayed_total` increases only for recovered records, timestamps remain unchanged, and the worker reports `running`.
6. Repeat a short outage during drain to verify fresh-record priority and bounded oldest-first replay. Export the full history and retain it with service logs.
