import json
import os
import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from src.telemetry_spool import (
    DeliveryResult,
    DeliveryStatus,
    DeliveryWorker,
    SpoolError,
    SpoolIntegrityError,
    SpoolRepository,
)


def payload(timestamp: str = "2026-08-16T10:00:00Z") -> dict[str, object]:
    return {
        "schema_version": "senior-pomidor.edge.telemetry.v2",
        "device_id": "edge-01",
        "timestamp_utc": timestamp,
        "pods": {},
        "system_health": {},
    }


def repository(tmp_path, **kwargs) -> SpoolRepository:
    defaults = {
        "disk_warning_percent": 101,
        "disk_degraded_percent": 102,
        "disk_critical_percent": 103,
    }
    defaults.update(kwargs)
    return SpoolRepository(tmp_path / "spool.sqlite3", boot_id="boot-a", **defaults).open()


def test_enqueue_allocates_sequence_and_identity_atomically(tmp_path) -> None:
    spool = repository(tmp_path)
    first = spool.enqueue(payload())
    second = spool.enqueue(payload("2026-08-16T10:01:00Z"))

    assert (first.sequence, second.sequence) == (1, 2)
    assert first.payload["record_id"] == first.record_id
    assert first.payload["boot_id"] == "boot-a"
    assert first.payload["stored_at_utc"] == first.stored_at
    assert spool.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert spool.connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_v1_database_migrates_transactionally_without_losing_records(tmp_path) -> None:
    path = tmp_path / "spool.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE records (
            record_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, observed_at TEXT NOT NULL, stored_at TEXT NOT NULL,
            payload_json TEXT NOT NULL, state TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT, in_flight_at TEXT, delivered_at TEXT, dead_letter_at TEXT,
            last_error TEXT, legacy_hash TEXT UNIQUE, UNIQUE(device_id, sequence)
        );
        CREATE TABLE delivery_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL REFERENCES records(record_id),
            attempted_at TEXT NOT NULL, completed_at TEXT NOT NULL, result TEXT NOT NULL,
            detail TEXT, http_status INTEGER
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO records VALUES(
            'v1-record','edge-01','boot-v1',1,'2026-08-16T10:00:00Z','2026-08-16T10:00:01Z',
            '{"device_id":"edge-01","timestamp_utc":"2026-08-16T10:00:00Z"}',
            'pending',1,NULL,NULL,NULL,NULL,'timeout',NULL
        );
        INSERT INTO delivery_attempts(record_id,attempted_at,completed_at,result,detail,http_status)
        VALUES('v1-record','2026-08-16T10:00:02Z','2026-08-16T10:00:03Z','retry','timeout',503);
        PRAGMA user_version=1;
        """
    )
    connection.close()

    spool = repository(tmp_path)

    assert spool.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert spool.get("v1-record").observed_at == "2026-08-16T10:00:00Z"
    assert spool.health()["written_total"] == 1
    assert spool.health()["failure_total"] == 1


def test_restart_recovers_in_flight_and_preserves_sequence(tmp_path) -> None:
    spool = repository(tmp_path)
    first = spool.enqueue(payload())
    assert spool.claim_batch()[0].state == "in_flight"
    spool.close()

    restarted = SpoolRepository(
        tmp_path / "spool.sqlite3",
        boot_id="boot-b",
        disk_warning_percent=101,
        disk_degraded_percent=102,
        disk_critical_percent=103,
    ).open()
    assert restarted.get(first.record_id).state == "pending"
    assert restarted.enqueue(payload()).sequence == 2
    assert restarted.get(first.record_id).boot_id == "boot-a"


@pytest.mark.parametrize("status", [DeliveryStatus.ACCEPTED, DeliveryStatus.DUPLICATE])
def test_only_matching_success_ack_delivers(tmp_path, status) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload())
    spool.claim_batch()

    assert spool.complete_attempt(record.record_id, DeliveryResult(status, record.record_id)) == "delivered"


def test_lost_or_mismatched_ack_retries_with_persistent_history(tmp_path) -> None:
    spool = repository(tmp_path, retry_jitter=0)
    record = spool.enqueue(payload())
    spool.claim_batch()
    state = spool.complete_attempt(
        record.record_id,
        DeliveryResult(DeliveryStatus.ACCEPTED, "another-record"),
        random_value=0.5,
    )

    assert state == "pending"
    assert spool.get(record.record_id).attempt_count == 1
    assert spool.attempts(record.record_id)[0]["result"] == "retry"


def test_rejected_and_exhausted_attempts_enter_dead_letter(tmp_path) -> None:
    spool = repository(tmp_path, max_attempts=1)
    rejected = spool.enqueue(payload())
    retry_exhausted = spool.enqueue(payload("2026-08-16T10:01:00Z"))
    spool.claim_batch()

    assert (
        spool.complete_attempt(rejected.record_id, DeliveryResult(DeliveryStatus.REJECTED, rejected.record_id))
        == "dead_letter"
    )
    assert (
        spool.complete_attempt(
            retry_exhausted.record_id, DeliveryResult(DeliveryStatus.RETRY, retry_exhausted.record_id)
        )
        == "dead_letter"
    )
    assert spool.retry_dead(rejected.record_id) == 1
    assert spool.get(rejected.record_id).state == "pending"


def test_claim_prioritizes_newest_then_oldest_backlog(tmp_path) -> None:
    spool = repository(tmp_path)
    records = [spool.enqueue(payload(f"2026-08-16T10:0{index}:00Z")) for index in range(4)]

    claimed = spool.claim_batch(3)

    assert [item.record_id for item in claimed] == [records[3].record_id, records[0].record_id, records[1].record_id]


def test_cleanup_only_deletes_old_delivered(tmp_path) -> None:
    spool = repository(tmp_path)
    delivered = spool.enqueue(payload())
    pending = spool.enqueue(payload("2026-08-16T10:01:00Z"))
    dead = spool.enqueue(payload("2026-08-16T10:02:00Z"))
    spool.connection.execute(
        "UPDATE records SET state='delivered',delivered_at='2026-01-01T00:00:00Z' WHERE record_id=?",
        (delivered.record_id,),
    )
    spool.connection.execute(
        "UPDATE records SET state='dead_letter',dead_letter_at='2026-01-01T00:00:00Z' WHERE record_id=?",
        (dead.record_id,),
    )

    assert spool.cleanup_delivered(now=datetime(2026, 8, 16, tzinfo=UTC)) == 1
    assert spool.get(pending.record_id).state == "pending"
    assert spool.get(dead.record_id).state == "dead_letter"


def test_capacity_check_accounts_for_reusable_sqlite_pages(tmp_path) -> None:
    spool = repository(tmp_path, max_payload_bytes=512_000)
    large_payload = payload()
    large_payload["padding"] = "x" * 256_000
    delivered = spool.enqueue(large_payload)
    spool.connection.execute(
        "UPDATE records SET state='delivered',delivered_at='2026-01-01T00:00:00Z' WHERE record_id=?",
        (delivered.record_id,),
    )

    assert spool.cleanup_delivered(now=datetime(2026, 8, 16, tzinfo=UTC)) == 1
    assert spool.connection.execute("PRAGMA freelist_count").fetchone()[0] > 0
    spool.capacity_bytes = spool.total_db_bytes()

    assert spool.health()["status"] != "CRITICAL"
    assert spool.enqueue(payload("2026-08-16T10:01:00Z")).sequence == 2


def test_capacity_check_rejects_when_effective_space_is_exhausted(tmp_path) -> None:
    spool = repository(tmp_path)
    spool.capacity_bytes = 0

    with pytest.raises(SpoolError, match="capacity exhausted"):
        spool.enqueue(payload())


def test_payload_limit_is_enforced_before_commit(tmp_path) -> None:
    spool = repository(tmp_path, max_payload_bytes=1024)
    oversized = payload()
    oversized["padding"] = "x" * 2048

    with pytest.raises(SpoolError, match="MAX_PAYLOAD_BYTES"):
        spool.enqueue(oversized)

    assert spool.health()["written_total"] == 0
    assert spool.list_records(limit=None) == []


def test_legacy_import_is_deterministic_commit_before_delete_and_keeps_corrupt(tmp_path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    valid = legacy / "valid.json"
    valid.write_text(json.dumps(payload()), encoding="utf-8")
    corrupt = legacy / "corrupt.json"
    corrupt.write_text("{invalid", encoding="utf-8")
    os.utime(valid, (1, 1))
    os.utime(corrupt, (2, 2))
    spool = repository(tmp_path)

    imported, invalid = spool.import_legacy(legacy)

    assert imported == 1
    assert not valid.exists()
    assert invalid == [corrupt]
    assert corrupt.exists()
    record = spool.list_records()[0]
    assert record.record_id.startswith("legacy-")
    assert spool.import_legacy(legacy)[0] == 0


def test_existing_corrupt_database_is_never_replaced(tmp_path) -> None:
    path = tmp_path / "spool.sqlite3"
    original = b"not a sqlite database"
    path.write_bytes(original)

    with pytest.raises(SpoolIntegrityError):
        SpoolRepository(path).open()

    assert path.read_bytes() == original


def test_locked_database_does_not_partially_allocate_sequence(tmp_path) -> None:
    spool = repository(tmp_path, busy_timeout_ms=1)
    lock = sqlite3.connect(spool.path, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError):
        spool.enqueue(payload())
    lock.execute("ROLLBACK")

    assert spool.enqueue(payload()).sequence == 1


def test_health_reports_backlog_dead_letters_attempts_and_sizes(tmp_path) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload())
    spool.claim_batch()
    spool.complete_attempt(record.record_id, DeliveryResult(DeliveryStatus.REJECTED, record.record_id))
    health = spool.health()

    assert health["status"] == "DEGRADED"
    assert health["dead_letter_count"] == 1
    assert health["delivery_attempt_count"] == 1
    assert health["database_size_bytes"] > 0
    assert health["free_space_bytes"] > 0
    assert health["written_total"] == 1
    assert health["failure_total"] == 1
    assert health["last_error_code"] == "server_rejected"


def test_success_metrics_survive_cleanup_and_replay_counts_only_success(tmp_path) -> None:
    spool = repository(tmp_path, retry_jitter=0)
    record = spool.enqueue(payload())
    spool.claim_batch()
    spool.start_attempt(record.record_id, datetime(2026, 8, 16, 10, 0, 1, tzinfo=UTC))
    spool.complete_attempt(
        record.record_id,
        DeliveryResult(DeliveryStatus.RETRY, record.record_id, error_code="transport_error"),
        random_value=0.5,
    )
    spool.connection.execute("UPDATE records SET next_attempt_at=NULL WHERE record_id=?", (record.record_id,))
    spool.claim_batch()
    spool.start_attempt(record.record_id, datetime(2026, 8, 16, 10, 0, 2, tzinfo=UTC))
    spool.complete_attempt(record.record_id, DeliveryResult(DeliveryStatus.DUPLICATE, record.record_id))
    spool.connection.execute(
        "UPDATE records SET delivered_at='2026-01-01T00:00:00Z' WHERE record_id=?", (record.record_id,)
    )

    assert spool.cleanup_delivered(now=datetime(2026, 8, 16, tzinfo=UTC)) == 1
    health = spool.health()
    assert health["success_total"] == 1
    assert health["failure_total"] == 1
    assert health["duplicate_total"] == 1
    assert health["replayed_total"] == 1
    assert health["last_successful_delivery_at_utc"] is not None


def test_mixed_batch_results_are_committed_independently(tmp_path) -> None:
    spool = repository(tmp_path, retry_jitter=0)
    records = [spool.enqueue(payload(f"2026-08-16T10:0{index}:00Z")) for index in range(3)]
    results = {
        records[0].record_id: DeliveryStatus.ACCEPTED,
        records[1].record_id: DeliveryStatus.REJECTED,
        records[2].record_id: DeliveryStatus.RETRY,
    }
    worker = DeliveryWorker(spool, MappingSender(results), NullMqttSender(), batch_size=3)

    assert worker.deliver_once() == 3
    assert spool.get(records[0].record_id).state == "delivered"
    assert spool.get(records[1].record_id).state == "dead_letter"
    assert spool.get(records[2].record_id).state == "pending"
    assert all(spool.get(record.record_id).first_attempt_at for record in records)


def test_worker_releases_unprocessed_claims_when_batch_aborts(tmp_path, monkeypatch) -> None:
    spool = repository(tmp_path)
    records = [spool.enqueue(payload(f"2026-08-16T10:0{index}:00Z")) for index in range(3)]
    worker = DeliveryWorker(spool, AcceptingSender(), NullMqttSender(), batch_size=3)

    def fail_completion(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated commit failure")

    monkeypatch.setattr(spool, "complete_attempt", fail_completion)
    with pytest.raises(sqlite3.OperationalError, match="simulated commit failure"):
        worker.deliver_once()

    assert [spool.get(record.record_id).state for record in records] == ["pending", "pending", "pending"]


def test_worker_supervisor_restarts_after_unexpected_failure(tmp_path, monkeypatch) -> None:
    spool = repository(tmp_path)
    worker = DeliveryWorker(spool, AcceptingSender(), NullMqttSender())
    original = worker.deliver_once
    restarted = threading.Event()
    calls = 0

    def flaky(repository=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated worker crash")
        restarted.set()
        return original(repository)

    monkeypatch.setattr(worker, "deliver_once", flaky)
    worker.start()
    try:
        assert restarted.wait(3)
        assert worker._thread is not None and worker._thread.is_alive()
        assert spool.health()["worker_state"] == "running"
    finally:
        worker.stop()


def test_online_backup_and_full_export_include_attempt_history(tmp_path) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload())
    spool.claim_batch()
    spool.complete_attempt(record.record_id, DeliveryResult(DeliveryStatus.ACCEPTED, record.record_id))
    backup_path = tmp_path / "backup" / "spool.sqlite3"
    export_path = tmp_path / "export" / "records.jsonl"

    assert spool.online_backup(backup_path) == backup_path
    assert spool.export_records(export_path) == 1

    backup = sqlite3.connect(backup_path)
    try:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
    finally:
        backup.close()
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["record"]["record_id"] == record.record_id
    assert exported["attempts"][0]["result"] == "accepted"


def test_delivery_rate_limit_is_enforced_across_single_record_batches(tmp_path, monkeypatch) -> None:
    spool = repository(tmp_path)
    spool.enqueue(payload())
    spool.enqueue(payload("2026-08-16T10:01:00Z"))
    clock = [10.0]
    sleeps = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr("src.telemetry_spool.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("src.telemetry_spool.time.sleep", sleep)
    worker = DeliveryWorker(
        spool,
        AcceptingSender(),
        NullMqttSender(),
        batch_size=1,
        rate_limit_per_second=2,
    )

    assert worker.deliver_once() == 1
    assert worker.deliver_once() == 1
    assert sleeps == [pytest.approx(0.5)]

    clock[0] += 1
    spool.enqueue(payload("2026-08-16T10:02:00Z"))
    assert worker.deliver_once() == 1
    assert sleeps == [pytest.approx(0.5)]


class AcceptingSender:
    def send(self, payload):
        return DeliveryResult(DeliveryStatus.ACCEPTED, payload["record_id"])


class NullMqttSender:
    def publish(self, _payload):
        return True


class MappingSender:
    def __init__(self, results):
        self.results = results

    def send(self, payload):
        return DeliveryResult(self.results[payload["record_id"]], payload["record_id"])
