import json
import os
import sqlite3
import subprocess
import sys
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
    assert spool.connection.execute("PRAGMA user_version").fetchone()[0] == 3


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

    assert spool.connection.execute("PRAGMA user_version").fetchone()[0] == 3
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


def test_v2_database_migrates_to_v3_without_losing_payload_or_attempts(tmp_path) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload(), record_id="legacy-" + "a" * 64, legacy_hash="a" * 64)
    spool.claim_batch()
    spool.complete_attempt(record.record_id, DeliveryResult(DeliveryStatus.RETRY, record.record_id))
    expected_payload = spool.get(record.record_id).payload
    expected_attempts = spool.attempts(record.record_id)
    spool.close()

    connection = sqlite3.connect(tmp_path / "spool.sqlite3")
    connection.executescript("DROP TABLE dead_letter_resolutions; PRAGMA user_version=2;")
    connection.close()

    migrated = repository(tmp_path)
    assert migrated.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert migrated.get(record.record_id).payload == expected_payload
    assert migrated.attempts(record.record_id) == expected_attempts
    migrated.close()

    reopened = repository(tmp_path)
    assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert reopened.get(record.record_id).payload == expected_payload


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


def test_resolve_dead_is_atomic_audited_and_logically_reconciled(tmp_path) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload(), record_id="legacy-" + "b" * 64, legacy_hash="b" * 64)
    spool.claim_batch()
    spool.complete_attempt(
        record.record_id,
        DeliveryResult(
            DeliveryStatus.REJECTED,
            record.record_id,
            detail="server rejected legacy payload",
            error_code="server_rejected",
        ),
    )
    before = spool.health()
    resolved_at = datetime(2026, 8, 16, 11, 0, tzinfo=UTC)

    result = spool.resolve_dead(
        record.record_id,
        reason="already_present_on_server",
        evidence="server telemetry_events.id=123",
        now=resolved_at,
    )

    assert result == {
        "record_id": record.record_id,
        "state": "reconciled",
        "reason": "already_present_on_server",
        "evidence": "server telemetry_events.id=123",
        "resolved_at": "2026-08-16T11:00:00Z",
        "changed": True,
    }
    reconciled = spool.get(record.record_id)
    assert reconciled.state == "reconciled"
    assert reconciled.attempt_count == 1
    assert reconciled.last_error_code is None
    audit = spool.resolution(record.record_id)
    assert audit == {
        "record_id": record.record_id,
        "resolved_at": "2026-08-16T11:00:00Z",
        "reason": "already_present_on_server",
        "evidence": "server telemetry_events.id=123",
        "attempt_count": 1,
        "last_error_code": "server_rejected",
        "last_error": "server rejected legacy payload",
        "dead_letter_at": audit["dead_letter_at"],
    }
    assert audit["dead_letter_at"] is not None
    row = spool.connection.execute(
        "SELECT state,delivered_at,dead_letter_at,last_error,last_error_code FROM records WHERE record_id=?",
        (record.record_id,),
    ).fetchone()
    assert tuple(row) == ("delivered", "2026-08-16T11:00:00Z", None, None, None)

    health = spool.health()
    assert health["status"] == "OK"
    assert health["dead_letter_count"] == 0
    assert health["reconciled_count"] == 1
    assert health["resolution_total"] == 1
    assert health["last_reconciliation_at_utc"] == "2026-08-16T11:00:00Z"
    assert health["last_reconciliation_reason"] == "already_present_on_server"
    for key in ("delivery_attempt_count", "success_total", "duplicate_total", "replayed_total"):
        assert health[key] == before[key]


def test_resolve_dead_is_idempotent_and_rejects_conflicts(tmp_path) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload(), record_id="legacy-" + "c" * 64, legacy_hash="c" * 64)
    spool.connection.execute(
        "UPDATE records SET state='dead_letter',attempt_count=2,dead_letter_at=? WHERE record_id=?",
        ("2026-08-16T10:30:00Z", record.record_id),
    )
    kwargs = {
        "reason": "already_present_on_server",
        "evidence": "server telemetry_events.id=456",
    }

    assert spool.resolve_dead(record.record_id, **kwargs)["changed"] is True
    assert spool.resolve_dead(record.record_id, **kwargs)["changed"] is False
    with pytest.raises(SpoolError, match="different reason or evidence"):
        spool.resolve_dead(record.record_id, reason=kwargs["reason"], evidence="change-record=other")

    assert spool.resolution(record.record_id)["evidence"] == kwargs["evidence"]


@pytest.mark.parametrize(
    ("record_id", "reason", "evidence", "error"),
    [
        ("not-legacy", "already_present_on_server", "server id=1", "legacy"),
        ("legacy-" + "d" * 64, "unknown", "server id=1", "unsupported"),
        ("legacy-" + "d" * 64, "already_present_on_server", "   ", "non-empty"),
        ("legacy-" + "d" * 64, "already_present_on_server", "x" * 1025, "1024"),
    ],
)
def test_resolve_dead_validates_operator_input(tmp_path, record_id, reason, evidence, error) -> None:
    spool = repository(tmp_path)
    with pytest.raises(SpoolError, match=error):
        spool.resolve_dead(record_id, reason=reason, evidence=evidence)
    assert spool.connection.execute("SELECT COUNT(*) FROM dead_letter_resolutions").fetchone()[0] == 0


def test_resolve_dead_rejects_missing_or_non_dead_record(tmp_path) -> None:
    spool = repository(tmp_path)
    missing_id = "legacy-" + "e" * 64
    with pytest.raises(KeyError, match=missing_id):
        spool.resolve_dead(
            missing_id,
            reason="already_present_on_server",
            evidence="server telemetry_events.id=1",
        )
    pending = spool.enqueue(payload(), record_id="legacy-" + "f" * 64, legacy_hash="f" * 64)
    with pytest.raises(SpoolError, match="dead_letter"):
        spool.resolve_dead(
            pending.record_id,
            reason="already_present_on_server",
            evidence="server telemetry_events.id=1",
        )
    assert spool.connection.execute("SELECT COUNT(*) FROM dead_letter_resolutions").fetchone()[0] == 0


def test_reconciled_record_is_not_retried_claimed_or_cleaned_up(tmp_path) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload(), record_id="legacy-" + "1" * 64, legacy_hash="1" * 64)
    spool.connection.execute(
        "UPDATE records SET state='dead_letter',dead_letter_at='2026-01-01T00:00:00Z' WHERE record_id=?",
        (record.record_id,),
    )
    spool.resolve_dead(
        record.record_id,
        reason="already_present_on_server",
        evidence="server telemetry_events.id=789",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert spool.retry_dead(record.record_id) == 0
    assert spool.retry_dead() == 0
    assert spool.claim_batch() == []
    assert spool.cleanup_delivered(now=datetime(2026, 8, 16, tzinfo=UTC)) == 0
    assert [item.record_id for item in spool.list_records(state="reconciled")] == [record.record_id]
    assert spool.list_records(state="delivered") == []
    assert spool.get(record.record_id).payload["record_id"] == record.record_id

    export_path = tmp_path / "reconciled.jsonl"
    backup_path = tmp_path / "backup.sqlite3"
    assert spool.export_records(export_path, state="reconciled") == 1
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["record"]["state"] == "reconciled"
    assert exported["resolution"]["evidence"] == "server telemetry_events.id=789"
    spool.online_backup(backup_path)
    backup = sqlite3.connect(backup_path)
    try:
        assert backup.execute("SELECT evidence FROM dead_letter_resolutions").fetchone()[0] == (
            "server telemetry_events.id=789"
        )
        assert backup.execute("SELECT payload_json FROM records WHERE record_id=?", (record.record_id,)).fetchone()
    finally:
        backup.close()

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        spool.connection.execute(
            "UPDATE dead_letter_resolutions SET evidence='changed' WHERE record_id=?",
            (record.record_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        spool.connection.execute("DELETE FROM dead_letter_resolutions WHERE record_id=?", (record.record_id,))


def test_resolve_dead_cli_reports_reconciliation_and_show_includes_audit(tmp_path) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload(), record_id="legacy-" + "4" * 64, legacy_hash="4" * 64)
    spool.connection.execute(
        "UPDATE records SET state='dead_letter',dead_letter_at='2026-01-01T00:00:00Z' WHERE record_id=?",
        (record.record_id,),
    )
    spool.close()
    command = [
        sys.executable,
        "scripts/telemetry_spool.py",
        "--db",
        str(tmp_path / "spool.sqlite3"),
        "resolve-dead",
        record.record_id,
        "--reason",
        "already_present_on_server",
        "--evidence",
        "server telemetry_events.id=1000",
    ]

    first = subprocess.run(command, check=True, capture_output=True, text=True)
    repeated = subprocess.run(command, check=True, capture_output=True, text=True)
    show = subprocess.run(
        [
            sys.executable,
            "scripts/telemetry_spool.py",
            "--db",
            str(tmp_path / "spool.sqlite3"),
            "show",
            record.record_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(first.stdout)["changed"] is True
    assert json.loads(repeated.stdout)["changed"] is False
    shown = json.loads(show.stdout)
    assert shown["record"]["state"] == "reconciled"
    assert shown["resolution"]["reason"] == "already_present_on_server"


def test_resolution_and_state_transition_roll_back_together(tmp_path) -> None:
    spool = repository(tmp_path)
    record = spool.enqueue(payload(), record_id="legacy-" + "2" * 64, legacy_hash="2" * 64)
    spool.connection.execute(
        "UPDATE records SET state='dead_letter',dead_letter_at='2026-01-01T00:00:00Z' WHERE record_id=?",
        (record.record_id,),
    )
    spool.connection.execute(
        """CREATE TRIGGER fail_resolution_transition BEFORE UPDATE OF state ON records
        WHEN OLD.record_id='legacy-"""
        + "2" * 64
        + "' BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="simulated failure"):
        spool.resolve_dead(
            record.record_id,
            reason="already_present_on_server",
            evidence="server telemetry_events.id=999",
        )

    assert spool.get(record.record_id).state == "dead_letter"
    assert spool.resolution(record.record_id) is None


def test_locked_database_cannot_partially_resolve_dead(tmp_path) -> None:
    spool = repository(tmp_path, busy_timeout_ms=1)
    record = spool.enqueue(payload(), record_id="legacy-" + "3" * 64, legacy_hash="3" * 64)
    spool.connection.execute(
        "UPDATE records SET state='dead_letter',dead_letter_at='2026-01-01T00:00:00Z' WHERE record_id=?",
        (record.record_id,),
    )
    lock = sqlite3.connect(spool.path, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            spool.resolve_dead(
                record.record_id,
                reason="already_present_on_server",
                evidence="server telemetry_events.id=999",
            )
    finally:
        lock.execute("ROLLBACK")
        lock.close()

    assert spool.get(record.record_id).state == "dead_letter"
    assert spool.resolution(record.record_id) is None


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
