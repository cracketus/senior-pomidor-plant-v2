"""Durable SQLite telemetry spool and delivery state machine."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3
FINAL_STATES = ("delivered", "dead_letter")
RESOLUTION_REASON_ALREADY_PRESENT = "already_present_on_server"
MAX_RESOLUTION_EVIDENCE_LENGTH = 1024


class SpoolError(RuntimeError):
    """Base error for durable spool failures."""


class SpoolIntegrityError(SpoolError):
    """Raised when an existing database fails SQLite integrity checks."""


class DeliveryStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    RETRY = "retry"
    REJECTED = "rejected"


class DeliveryErrorCode(StrEnum):
    TRANSPORT_ERROR = "transport_error"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"
    INVALID_ACK_STATUS = "invalid_ack_status"
    ACK_RECORD_ID_MISMATCH = "ack_record_id_mismatch"
    DELIVERY_DISABLED = "delivery_disabled"
    MISSING_URL = "missing_url"
    SERVER_REJECTED = "server_rejected"
    SERVER_RETRY = "server_retry"
    RETRY_EXHAUSTED = "retry_exhausted"


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus
    record_id: str | None
    detail: str | None = None
    http_status: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class SpoolRecord:
    record_id: str
    device_id: str
    boot_id: str
    sequence: int
    observed_at: str
    stored_at: str
    payload: dict[str, Any]
    state: str
    attempt_count: int
    next_attempt_at: str | None
    first_attempt_at: str | None
    last_attempt_at: str | None
    last_error_code: str | None


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_text(value: datetime | None = None) -> str:
    value = value or utc_now()
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if value:
            return value
    except OSError:
        pass
    return str(uuid.uuid4())


class SpoolRepository:
    """SQLite repository. A repository instance owns one connection."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        capacity_mb: int = 1536,
        retry_schedule: tuple[int, ...] = (5, 15, 30, 60, 300),
        retry_jitter: float = 0.2,
        max_attempts: int | None = None,
        disk_warning_percent: int = 80,
        disk_degraded_percent: int = 90,
        disk_critical_percent: int = 95,
        max_payload_bytes: int = 16384,
        poll_interval_seconds: int = 60,
        recover_in_flight: bool = True,
        boot_id: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self.capacity_bytes = capacity_mb * 1024 * 1024
        self.retry_schedule = retry_schedule
        self.retry_jitter = retry_jitter
        self.max_attempts = max_attempts
        self.disk_warning_percent = disk_warning_percent
        self.disk_degraded_percent = disk_degraded_percent
        self.disk_critical_percent = disk_critical_percent
        self.max_payload_bytes = max_payload_bytes
        self.poll_interval_seconds = poll_interval_seconds
        self.recover_in_flight = recover_in_flight
        self.boot_id = boot_id or read_boot_id()
        self.logger = logger or logging.getLogger(__name__)
        self._connection: sqlite3.Connection | None = None

    def open(self) -> SpoolRepository:
        existed = self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            if existed:
                result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                self.logger.info("Telemetry spool integrity result: %s", result)
                if result != "ok":
                    connection.close()
                    raise SpoolIntegrityError(f"telemetry spool quick_check failed: {result}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._connection = connection
            self._migrate()
            for counter in (
                "written_total",
                "success_total",
                "failure_total",
                "duplicate_total",
                "replayed_total",
                "write_failure_total",
            ):
                connection.execute(
                    "INSERT INTO metadata(key,value) VALUES(?, '0') ON CONFLICT(key) DO NOTHING",
                    (counter,),
                )
            if self.recover_in_flight:
                recovered = connection.execute(
                    "UPDATE records SET state='pending', in_flight_at=NULL, "
                    "recovery_count=recovery_count+1 WHERE state='in_flight'"
                ).rowcount
                if recovered:
                    self.logger.warning("Recovered %s in-flight telemetry records", recovered)
            self.set_metadata("current_boot_id", self.boot_id)
            return self
        except sqlite3.DatabaseError as exc:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            elif connection is not None:
                connection.close()
            raise SpoolIntegrityError(f"cannot open telemetry spool {self.path}: {exc}") from exc
        except SpoolError:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            elif connection is not None:
                connection.close()
            raise

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def worker_connection(self) -> SpoolRepository:
        return SpoolRepository(
            self.path,
            busy_timeout_ms=self.busy_timeout_ms,
            capacity_mb=max(1, self.capacity_bytes // (1024 * 1024)),
            retry_schedule=self.retry_schedule,
            retry_jitter=self.retry_jitter,
            max_attempts=self.max_attempts,
            disk_warning_percent=self.disk_warning_percent,
            disk_degraded_percent=self.disk_degraded_percent,
            disk_critical_percent=self.disk_critical_percent,
            max_payload_bytes=self.max_payload_bytes,
            poll_interval_seconds=self.poll_interval_seconds,
            recover_in_flight=False,
            boot_id=self.boot_id,
            logger=self.logger,
        ).open()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise SpoolError("telemetry spool is not open")
        return self._connection

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise SpoolError(f"unsupported telemetry spool schema version {version}")
        if version == 0:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE records (
                    record_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    boot_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    stored_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','in_flight','delivered','dead_letter')),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    in_flight_at TEXT,
                    delivered_at TEXT,
                    dead_letter_at TEXT,
                    last_error TEXT,
                    first_attempt_at TEXT,
                    last_attempt_at TEXT,
                    last_error_code TEXT,
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    legacy_hash TEXT UNIQUE,
                    UNIQUE(device_id, sequence)
                );
                CREATE TABLE delivery_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL REFERENCES records(record_id) ON DELETE CASCADE,
                    attempted_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    result TEXT NOT NULL,
                    detail TEXT,
                    http_status INTEGER,
                    error_code TEXT
                );
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE dead_letter_resolutions (
                    record_id TEXT PRIMARY KEY REFERENCES records(record_id),
                    resolved_at TEXT NOT NULL,
                    reason TEXT NOT NULL CHECK(reason='already_present_on_server'),
                    evidence TEXT NOT NULL CHECK(length(trim(evidence)) > 0 AND length(evidence) <= 1024),
                    attempt_count INTEGER NOT NULL,
                    last_error_code TEXT,
                    last_error TEXT,
                    dead_letter_at TEXT
                );
                CREATE TRIGGER dead_letter_resolutions_immutable_update
                BEFORE UPDATE ON dead_letter_resolutions
                BEGIN SELECT RAISE(ABORT, 'dead-letter resolution audit is immutable'); END;
                CREATE TRIGGER dead_letter_resolutions_immutable_delete
                BEFORE DELETE ON dead_letter_resolutions
                BEGIN SELECT RAISE(ABORT, 'dead-letter resolution audit is immutable'); END;
                CREATE INDEX records_delivery_idx ON records(state, next_attempt_at, stored_at);
                CREATE INDEX attempts_record_idx ON delivery_attempts(record_id, id);
                PRAGMA user_version=3;
                COMMIT;
                """
            )
            version = 3
        if version == 1:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE records ADD COLUMN first_attempt_at TEXT;
                ALTER TABLE records ADD COLUMN last_attempt_at TEXT;
                ALTER TABLE records ADD COLUMN last_error_code TEXT;
                ALTER TABLE records ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE delivery_attempts ADD COLUMN error_code TEXT;
                INSERT OR IGNORE INTO metadata(key,value)
                    SELECT 'written_total',CAST(COUNT(*) AS TEXT) FROM records
                    ;
                INSERT OR IGNORE INTO metadata(key,value)
                    SELECT 'success_total',CAST(COUNT(*) AS TEXT) FROM delivery_attempts
                    WHERE result IN ('accepted','duplicate');
                INSERT OR IGNORE INTO metadata(key,value)
                    SELECT 'failure_total',CAST(COUNT(*) AS TEXT) FROM delivery_attempts
                    WHERE result IN ('retry','rejected');
                INSERT OR IGNORE INTO metadata(key,value)
                    SELECT 'duplicate_total',CAST(COUNT(*) AS TEXT) FROM delivery_attempts
                    WHERE result='duplicate';
                INSERT OR IGNORE INTO metadata(key,value) VALUES('replayed_total','0');
                PRAGMA user_version=2;
                COMMIT;
                """
            )
            version = 2
        if version == 2:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE dead_letter_resolutions (
                    record_id TEXT PRIMARY KEY REFERENCES records(record_id),
                    resolved_at TEXT NOT NULL,
                    reason TEXT NOT NULL CHECK(reason='already_present_on_server'),
                    evidence TEXT NOT NULL CHECK(length(trim(evidence)) > 0 AND length(evidence) <= 1024),
                    attempt_count INTEGER NOT NULL,
                    last_error_code TEXT,
                    last_error TEXT,
                    dead_letter_at TEXT
                );
                CREATE TRIGGER dead_letter_resolutions_immutable_update
                BEFORE UPDATE ON dead_letter_resolutions
                BEGIN SELECT RAISE(ABORT, 'dead-letter resolution audit is immutable'); END;
                CREATE TRIGGER dead_letter_resolutions_immutable_delete
                BEFORE DELETE ON dead_letter_resolutions
                BEGIN SELECT RAISE(ABORT, 'dead-letter resolution audit is immutable'); END;
                PRAGMA user_version=3;
                COMMIT;
                """
            )

    def set_metadata(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_metadata(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def increment_counter(self, key: str, amount: int = 1) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+? AS TEXT)",
            (key, str(amount), amount),
        )

    def enqueue(
        self,
        payload: dict[str, Any],
        *,
        record_id: str | None = None,
        legacy_hash: str | None = None,
        stored_at: datetime | None = None,
    ) -> SpoolRecord:
        if self._capacity_used_bytes() >= self.capacity_bytes:
            raise SpoolError("telemetry spool capacity exhausted")
        if self.disk_usage_percent() >= self.disk_critical_percent:
            raise SpoolError("telemetry spool filesystem critical threshold reached")
        device_id = str(payload.get("device_id") or "")
        if not device_id:
            raise SpoolError("telemetry payload device_id is required")
        observed_at = str(payload.get("timestamp_utc") or utc_text())
        stored_text = utc_text(stored_at)
        record_id = record_id or str(uuid.uuid4())
        counter_key = f"sequence:{device_id}"
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM records WHERE record_id=?", (record_id,)).fetchone()
            if existing is not None:
                connection.execute("COMMIT")
                return self.get(record_id)
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (counter_key,)).fetchone()
            sequence = int(row[0]) + 1 if row else 1
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (counter_key, str(sequence)),
            )
            stored_payload = dict(payload)
            stored_payload.update(
                {
                    "record_id": record_id,
                    "boot_id": self.boot_id,
                    "sequence": sequence,
                    "stored_at_utc": stored_text,
                }
            )
            payload_json = json.dumps(stored_payload, separators=(",", ":"), sort_keys=True)
            if len(payload_json.encode("utf-8")) > self.max_payload_bytes:
                raise SpoolError(
                    f"telemetry payload exceeds TELEMETRY_SPOOL_MAX_PAYLOAD_BYTES={self.max_payload_bytes}"
                )
            connection.execute(
                """INSERT INTO records(
                    record_id,device_id,boot_id,sequence,observed_at,stored_at,payload_json,state,legacy_hash
                ) VALUES(?,?,?,?,?,?,?,'pending',?)""",
                (
                    record_id,
                    device_id,
                    self.boot_id,
                    sequence,
                    observed_at,
                    stored_text,
                    payload_json,
                    legacy_hash,
                ),
            )
            self.increment_counter("written_total")
            connection.execute("COMMIT")
            return self.get(record_id)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def import_legacy(self, directory: str | Path) -> tuple[int, list[Path]]:
        directory = Path(directory)
        imported = 0
        corrupt: list[Path] = []
        if not directory.exists():
            return imported, corrupt
        paths = sorted(directory.glob("*.json"), key=lambda item: (item.stat().st_mtime, item.name))
        for path in paths:
            digest: str | None = None
            try:
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                payload = json.loads(raw)
                if not isinstance(payload, dict) or not payload.get("device_id"):
                    raise ValueError("legacy telemetry must be a JSON object with device_id")
                record_id = f"legacy-{digest}"
                self.enqueue(payload, record_id=record_id, legacy_hash=digest)
                path.unlink()
                imported += 1
            except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError, SpoolError) as exc:
                if digest is not None:
                    exists = self.connection.execute(
                        "SELECT 1 FROM records WHERE record_id=?", (f"legacy-{digest}",)
                    ).fetchone()
                    if exists:
                        try:
                            path.unlink(missing_ok=True)
                            continue
                        except OSError:
                            pass
                self.logger.error("Legacy telemetry migration degraded for %s: %s", path, exc)
                corrupt.append(path)
        self.set_metadata("legacy_migration_corrupt_count", str(len(corrupt)))
        self.set_metadata("legacy_migration_completed_at", utc_text())
        return imported, corrupt

    def get(self, record_id: str) -> SpoolRecord:
        row = self.connection.execute(
            "SELECT records.*,dead_letter_resolutions.record_id AS resolution_record_id "
            "FROM records LEFT JOIN dead_letter_resolutions USING(record_id) WHERE records.record_id=?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return self._row_to_record(row)

    def list_records(
        self, *, state: str | None = None, limit: int | None = 100, sort: str = "newest"
    ) -> list[SpoolRecord]:
        if sort not in {"oldest", "newest"}:
            raise SpoolError("sort must be oldest or newest")
        direction = "ASC" if sort == "oldest" else "DESC"
        if state == "reconciled":
            where = " WHERE dead_letter_resolutions.record_id IS NOT NULL"
            parameters: list[Any] = []
        elif state == "delivered":
            where = " WHERE records.state=? AND dead_letter_resolutions.record_id IS NULL"
            parameters = [state]
        else:
            where = "" if state is None else " WHERE records.state=?"
            parameters = [] if state is None else [state]
        limit_sql = ""
        if limit is not None:
            if limit < 1:
                return []
            limit_sql = " LIMIT ?"
            parameters.append(limit)
        rows = self.connection.execute(
            "SELECT records.*,dead_letter_resolutions.record_id AS resolution_record_id "
            f"FROM records LEFT JOIN dead_letter_resolutions USING(record_id){where} "
            f"ORDER BY stored_at {direction},sequence {direction}{limit_sql}",
            parameters,
        )
        return [self._row_to_record(row) for row in rows]

    def claim_batch(self, limit: int = 10, now: datetime | None = None) -> list[SpoolRecord]:
        if limit < 1:
            return []
        now_text = utc_text(now)
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            eligible = "state='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?)"
            newest = connection.execute(
                f"SELECT record_id FROM records WHERE {eligible} ORDER BY stored_at DESC,sequence DESC LIMIT 1",
                (now_text,),
            ).fetchall()
            ids = [str(row[0]) for row in newest]
            if ids and limit > 1:
                placeholders = ",".join("?" for _ in ids)
                rows = connection.execute(
                    f"SELECT record_id FROM records WHERE {eligible} AND record_id NOT IN ({placeholders}) "
                    "ORDER BY stored_at ASC,sequence ASC LIMIT ?",
                    (now_text, *ids, limit - 1),
                ).fetchall()
                ids.extend(str(row[0]) for row in rows)
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE records SET state='in_flight',in_flight_at=? WHERE record_id IN ({placeholders})",
                    (now_text, *ids),
                )
            connection.execute("COMMIT")
            return [self.get(record_id) for record_id in ids]
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def start_attempt(self, record_id: str, now: datetime | None = None) -> datetime:
        """Persist the actual transport-attempt start before any network I/O."""
        started = now or utc_now()
        started_text = utc_text(started)
        updated = self.connection.execute(
            "UPDATE records SET first_attempt_at=COALESCE(first_attempt_at,?),last_attempt_at=? "
            "WHERE record_id=? AND state='in_flight'",
            (started_text, started_text, record_id),
        ).rowcount
        if updated != 1:
            raise SpoolError(f"record {record_id} is not in flight")
        return started

    def release_in_flight(self, record_ids: list[str] | None = None) -> int:
        if record_ids is None:
            return self.connection.execute(
                "UPDATE records SET state='pending',in_flight_at=NULL WHERE state='in_flight'"
            ).rowcount
        if not record_ids:
            return 0
        placeholders = ",".join("?" for _ in record_ids)
        return self.connection.execute(
            f"UPDATE records SET state='pending',in_flight_at=NULL "
            f"WHERE state='in_flight' AND record_id IN ({placeholders})",
            record_ids,
        ).rowcount

    def complete_attempt(
        self,
        record_id: str,
        result: DeliveryResult,
        *,
        attempted_at: datetime | None = None,
        random_value: float | None = None,
    ) -> str:
        now = utc_now()
        started = utc_text(attempted_at or now)
        row = self.connection.execute(
            "SELECT attempt_count,recovery_count FROM records WHERE record_id=?", (record_id,)
        ).fetchone()
        if row is None:
            raise KeyError(record_id)
        count = int(row[0]) + 1
        previous_count = int(row[0])
        recovery_count = int(row[1])
        valid_ack = result.record_id == record_id
        status = result.status if valid_ack else DeliveryStatus.RETRY
        detail = result.detail if valid_ack else "missing or mismatched record_id in acknowledgement"
        error_code = result.error_code if valid_ack else DeliveryErrorCode.ACK_RECORD_ID_MISMATCH.value
        if status in (DeliveryStatus.ACCEPTED, DeliveryStatus.DUPLICATE):
            state, next_attempt, delivered_at, dead_at = "delivered", None, utc_text(now), None
            error_code = None
        elif status is DeliveryStatus.REJECTED or (self.max_attempts is not None and count >= self.max_attempts):
            state, next_attempt, delivered_at, dead_at = "dead_letter", None, None, utc_text(now)
            if status is DeliveryStatus.REJECTED:
                error_code = error_code or DeliveryErrorCode.SERVER_REJECTED.value
            else:
                error_code = DeliveryErrorCode.RETRY_EXHAUSTED.value
        else:
            delay = self.retry_schedule[min(count - 1, len(self.retry_schedule) - 1)]
            unit = random.random() if random_value is None else random_value
            factor = 1 + ((unit * 2) - 1) * self.retry_jitter
            next_attempt = utc_text(now + timedelta(seconds=max(0, delay * factor)))
            state, delivered_at, dead_at = "pending", None, None
            error_code = error_code or DeliveryErrorCode.TRANSPORT_ERROR.value
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """INSERT INTO delivery_attempts(
                    record_id,attempted_at,completed_at,result,detail,http_status,error_code
                ) VALUES(?,?,?,?,?,?,?)""",
                (record_id, started, utc_text(now), status.value, detail, result.http_status, error_code),
            )
            connection.execute(
                """UPDATE records SET state=?,attempt_count=?,next_attempt_at=?,in_flight_at=NULL,
                   delivered_at=?,dead_letter_at=?,last_error=?,last_error_code=?,
                   first_attempt_at=COALESCE(first_attempt_at,?),last_attempt_at=COALESCE(last_attempt_at,?)
                   WHERE record_id=?""",
                (state, count, next_attempt, delivered_at, dead_at, detail, error_code, started, started, record_id),
            )
            self.set_metadata("last_delivery_result", status.value)
            if state == "delivered":
                self.increment_counter("success_total")
                if status is DeliveryStatus.DUPLICATE:
                    self.increment_counter("duplicate_total")
                if previous_count > 0 or recovery_count > 0:
                    self.increment_counter("replayed_total")
                self.set_metadata("last_successful_delivery_at", utc_text(now))
                remaining = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM records WHERE state IN ('pending','in_flight')"
                    ).fetchone()[0]
                )
                if remaining == 0:
                    self.set_metadata("outage_started_at", "")
            else:
                self.increment_counter("failure_total")
                self.set_metadata("last_delivery_error_code", str(error_code))
                self.set_metadata("last_delivery_error_detail", detail or "")
                self.set_metadata("last_delivery_error_at", utc_text(now))
                if state == "pending" and not self.get_metadata("outage_started_at"):
                    self.set_metadata("outage_started_at", utc_text(now))
                elif state == "dead_letter":
                    remaining = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM records WHERE state IN ('pending','in_flight')"
                        ).fetchone()[0]
                    )
                    if remaining == 0:
                        self.set_metadata("outage_started_at", "")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return state

    def retry_dead(self, record_id: str | None = None) -> int:
        if record_id:
            return self.connection.execute(
                "UPDATE records SET state='pending',next_attempt_at=NULL,dead_letter_at=NULL "
                "WHERE state='dead_letter' AND record_id=?",
                (record_id,),
            ).rowcount
        return self.connection.execute(
            "UPDATE records SET state='pending',next_attempt_at=NULL,dead_letter_at=NULL WHERE state='dead_letter'"
        ).rowcount

    def resolution(self, record_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT record_id,resolved_at,reason,evidence,attempt_count,last_error_code,last_error,dead_letter_at "
            "FROM dead_letter_resolutions WHERE record_id=?",
            (record_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def resolve_dead(
        self,
        record_id: str,
        *,
        reason: str,
        evidence: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not record_id.startswith("legacy-"):
            raise SpoolError("resolve-dead requires an exact legacy-* record_id")
        if reason != RESOLUTION_REASON_ALREADY_PRESENT:
            raise SpoolError(f"unsupported dead-letter resolution reason: {reason}")
        if not evidence.strip():
            raise SpoolError("dead-letter resolution evidence must be non-empty")
        if len(evidence) > MAX_RESOLUTION_EVIDENCE_LENGTH:
            raise SpoolError(f"dead-letter resolution evidence exceeds {MAX_RESOLUTION_EVIDENCE_LENGTH} characters")

        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT record_id,resolved_at,reason,evidence FROM dead_letter_resolutions WHERE record_id=?",
                (record_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["reason"]) != reason or str(existing["evidence"]) != evidence:
                    raise SpoolError("record already reconciled with different reason or evidence")
                connection.execute("COMMIT")
                return {**dict(existing), "state": "reconciled", "changed": False}

            record = connection.execute(
                "SELECT state,attempt_count,last_error_code,last_error,dead_letter_at FROM records WHERE record_id=?",
                (record_id,),
            ).fetchone()
            if record is None:
                raise KeyError(record_id)
            if str(record["state"]) != "dead_letter":
                raise SpoolError("record must be in dead_letter state")

            resolved_at = utc_text(now)
            connection.execute(
                """INSERT INTO dead_letter_resolutions(
                    record_id,resolved_at,reason,evidence,attempt_count,last_error_code,last_error,dead_letter_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    record_id,
                    resolved_at,
                    reason,
                    evidence,
                    int(record["attempt_count"]),
                    record["last_error_code"],
                    record["last_error"],
                    record["dead_letter_at"],
                ),
            )
            changed = connection.execute(
                """UPDATE records SET state='delivered',delivered_at=?,dead_letter_at=NULL,
                   last_error=NULL,last_error_code=NULL
                   WHERE record_id=? AND state='dead_letter'""",
                (resolved_at, record_id),
            ).rowcount
            if changed != 1:
                raise SpoolError("dead-letter state changed during reconciliation")
            connection.execute("COMMIT")
            return {
                "record_id": record_id,
                "state": "reconciled",
                "reason": reason,
                "evidence": evidence,
                "resolved_at": resolved_at,
                "changed": True,
            }
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def cleanup_delivered(self, retention_days: int = 7, now: datetime | None = None) -> int:
        cutoff = utc_text((now or utc_now()) - timedelta(days=retention_days))
        return self.connection.execute(
            "DELETE FROM records WHERE state='delivered' AND delivered_at<? "
            "AND NOT EXISTS (SELECT 1 FROM dead_letter_resolutions WHERE record_id=records.record_id)",
            (cutoff,),
        ).rowcount

    def checkpoint(self, truncate: bool = False) -> tuple[int, int, int]:
        mode = "TRUNCATE" if truncate else "PASSIVE"
        row = self.connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return int(row[0]), int(row[1]), int(row[2])

    def integrity_check(self) -> str:
        return str(self.connection.execute("PRAGMA quick_check").fetchone()[0])

    def attempts(self, record_id: str, *, sort: str = "oldest") -> list[dict[str, Any]]:
        if sort not in {"oldest", "newest"}:
            raise SpoolError("sort must be oldest or newest")
        direction = "ASC" if sort == "oldest" else "DESC"
        return [
            dict(row)
            for row in self.connection.execute(
                f"SELECT * FROM delivery_attempts WHERE record_id=? ORDER BY id {direction}",
                (record_id,),
            )
        ]

    def attempt_history(self, *, sort: str = "oldest") -> list[dict[str, Any]]:
        if sort not in {"oldest", "newest"}:
            raise SpoolError("sort must be oldest or newest")
        direction = "ASC" if sort == "oldest" else "DESC"
        return [
            dict(row) for row in self.connection.execute(f"SELECT * FROM delivery_attempts ORDER BY id {direction}")
        ]

    def online_backup(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
        finally:
            target.close()
        return destination

    def export_records(
        self,
        destination: str | Path,
        *,
        state: str | None = None,
        sort: str = "oldest",
    ) -> int:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        records = self.list_records(state=state, limit=None, sort=sort)
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                item = {
                    "record": {
                        "record_id": record.record_id,
                        "device_id": record.device_id,
                        "boot_id": record.boot_id,
                        "sequence": record.sequence,
                        "observed_at": record.observed_at,
                        "stored_at": record.stored_at,
                        "payload": record.payload,
                        "state": record.state,
                        "attempt_count": record.attempt_count,
                        "next_attempt_at": record.next_attempt_at,
                        "first_attempt_at": record.first_attempt_at,
                        "last_attempt_at": record.last_attempt_at,
                        "last_error_code": record.last_error_code,
                    },
                    "attempts": self.attempts(record.record_id),
                    "resolution": self.resolution(record.record_id),
                }
                stream.write(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n")
        return len(records)

    def total_db_bytes(self) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
            if candidate.exists()
        )

    def _capacity_used_bytes(self) -> int:
        page_size = int(self.connection.execute("PRAGMA page_size").fetchone()[0])
        reusable_pages = int(self.connection.execute("PRAGMA freelist_count").fetchone()[0])
        return max(0, self.total_db_bytes() - (page_size * reusable_pages))

    def disk_usage_percent(self) -> float:
        usage = shutil.disk_usage(self.path.parent)
        return 0.0 if usage.total == 0 else (usage.used / usage.total) * 100

    def relieve_disk_pressure(self, retention_days: int = 7) -> int:
        """Delete only eligible delivered data, then checkpoint the WAL."""
        if self.disk_usage_percent() < self.disk_warning_percent:
            return 0
        deleted = self.cleanup_delivered(retention_days)
        self.checkpoint(truncate=True)
        return deleted

    def health(self) -> dict[str, Any]:
        counts = {
            row[0]: int(row[1]) for row in self.connection.execute("SELECT state,COUNT(*) FROM records GROUP BY state")
        }
        oldest = self.connection.execute(
            "SELECT MIN(stored_at) FROM records WHERE state IN ('pending','in_flight')"
        ).fetchone()[0]
        result_counts = {
            str(row[0]): int(row[1])
            for row in self.connection.execute("SELECT result,COUNT(*) FROM delivery_attempts GROUP BY result")
        }
        free = shutil.disk_usage(self.path.parent).free
        corrupt = int(self.get_metadata("legacy_migration_corrupt_count") or 0)
        pending = counts.get("pending", 0)
        dead = counts.get("dead_letter", 0)
        disk_percent = self.disk_usage_percent()
        if self._capacity_used_bytes() >= self.capacity_bytes or disk_percent >= self.disk_critical_percent:
            disk_state = "CRITICAL"
            state = "CRITICAL"
        elif disk_percent >= self.disk_degraded_percent:
            disk_state = "DEGRADED"
            state = "DEGRADED"
        elif dead or corrupt or self.get_metadata("worker_state") == "error":
            disk_state = "WARNING" if disk_percent >= self.disk_warning_percent else "OK"
            state = "DEGRADED"
        elif disk_percent >= self.disk_warning_percent:
            disk_state = "WARNING"
            state = "BACKLOG"
        elif pending or counts.get("in_flight", 0):
            disk_state = "OK"
            state = "BACKLOG"
        else:
            disk_state = "OK"
            state = "OK"
        oldest_seconds = None
        if oldest:
            oldest_dt = datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
            oldest_seconds = max(0, int((utc_now() - oldest_dt).total_seconds()))
        outage_started = self.get_metadata("outage_started_at")
        outage_seconds = None
        if outage_started:
            outage_at = datetime.fromisoformat(outage_started.replace("Z", "+00:00"))
            outage_seconds = max(0, int((utc_now() - outage_at).total_seconds()))
        attempts = int(self.connection.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0])
        rate_limit = float(self.get_metadata("delivery_rate_limit") or 0)
        samples_per_day = 86400 / self.poll_interval_seconds
        retention_days = self.capacity_bytes / (self.max_payload_bytes * samples_per_day * 1.25)
        written_total = int(self.get_metadata("written_total") or 0)
        success_total = int(self.get_metadata("success_total") or 0)
        failure_total = int(self.get_metadata("failure_total") or 0)
        duplicate_total = int(self.get_metadata("duplicate_total") or 0)
        replayed_total = int(self.get_metadata("replayed_total") or 0)
        resolution_total = int(self.connection.execute("SELECT COUNT(*) FROM dead_letter_resolutions").fetchone()[0])
        last_resolution = self.connection.execute(
            "SELECT resolved_at,reason FROM dead_letter_resolutions ORDER BY resolved_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        last_success = self.get_metadata("last_successful_delivery_at") or None
        last_error_code = self.get_metadata("last_delivery_error_code") or None
        return {
            "status": state,
            "disk_status": disk_state,
            "pending_count": pending,
            "backlog_count": pending + counts.get("in_flight", 0),
            "in_flight_count": counts.get("in_flight", 0),
            "delivered_count": counts.get("delivered", 0) - resolution_total,
            "dead_letter_count": dead,
            "reconciled_count": resolution_total,
            "resolution_total": resolution_total,
            "last_reconciliation_at_utc": None if last_resolution is None else last_resolution["resolved_at"],
            "last_reconciliation_reason": None if last_resolution is None else last_resolution["reason"],
            "oldest_pending_age_seconds": oldest_seconds,
            "outage_duration_seconds": outage_seconds,
            "database_size_bytes": self.total_db_bytes(),
            "free_space_bytes": free,
            "disk_usage_percent": round(disk_percent, 1),
            "delivery_attempt_count": attempts,
            "delivery_success_count": success_total,
            "duplicate_count": duplicate_total,
            "delivery_retry_count": result_counts.get("retry", 0),
            "delivery_rejected_count": result_counts.get("rejected", 0),
            "last_delivery_result": self.get_metadata("last_delivery_result") or None,
            "last_delivery_at_utc": last_success,
            "last_successful_delivery_at_utc": last_success,
            "last_error_code": last_error_code,
            "last_error_detail": self.get_metadata("last_delivery_error_detail") or None,
            "last_error_at_utc": self.get_metadata("last_delivery_error_at") or None,
            "write_failure_count": int(self.get_metadata("write_failure_total") or 0),
            "written_total": written_total,
            "success_total": success_total,
            "failure_total": failure_total,
            "duplicate_total": duplicate_total,
            "replayed_total": replayed_total,
            "replay_count": replayed_total,
            "estimated_drain_seconds": round(pending / rate_limit, 1) if rate_limit > 0 else None,
            "estimated_retention_days": round(retention_days, 1),
            "worker_state": self.get_metadata("worker_state") or "not_started",
            "worker_last_heartbeat_at_utc": self.get_metadata("worker_last_heartbeat_at") or None,
            "worker_last_error": self.get_metadata("worker_last_error") or None,
            "legacy_corrupt_count": corrupt,
        }

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SpoolRecord:
        return SpoolRecord(
            record_id=str(row["record_id"]),
            device_id=str(row["device_id"]),
            boot_id=str(row["boot_id"]),
            sequence=int(row["sequence"]),
            observed_at=str(row["observed_at"]),
            stored_at=str(row["stored_at"]),
            payload=json.loads(row["payload_json"]),
            state="reconciled" if row["resolution_record_id"] is not None else str(row["state"]),
            attempt_count=int(row["attempt_count"]),
            next_attempt_at=row["next_attempt_at"],
            first_attempt_at=row["first_attempt_at"],
            last_attempt_at=row["last_attempt_at"],
            last_error_code=row["last_error_code"],
        )


class DeliveryWorker:
    """Background HTTP replay worker with MQTT as a best-effort mirror."""

    def __init__(
        self,
        repository: SpoolRepository,
        http_sender: Any,
        mqtt_sender: Any,
        *,
        batch_size: int = 10,
        rate_limit_per_second: float = 2.0,
        checkpoint_interval_seconds: int = 300,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repository = repository
        self.http_sender = http_sender
        self.mqtt_sender = mqtt_sender
        self.batch_size = batch_size
        self.rate_limit_per_second = rate_limit_per_second
        self.checkpoint_interval_seconds = checkpoint_interval_seconds
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_send_started_at: float | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.repository.set_metadata("delivery_rate_limit", str(self.rate_limit_per_second))
        self.repository.set_metadata("worker_state", "starting")
        self._thread = threading.Thread(target=self._run, name="telemetry-delivery", daemon=True)
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                self.logger.error("Telemetry delivery worker did not stop within %.1f seconds", timeout)
            else:
                self.repository.set_metadata("worker_state", "stopped")

    def deliver_once(self, repository: SpoolRepository | None = None) -> int:
        repository = repository or self.repository
        records = repository.claim_batch(self.batch_size)
        remaining = [record.record_id for record in records]
        try:
            for record in records:
                started = repository.start_attempt(record.record_id)
                with suppress(Exception):
                    self.mqtt_sender.publish(record.payload)
                self._wait_for_rate_limit()
                try:
                    result = self.http_sender.send(record.payload)
                except Exception as exc:  # noqa: BLE001 - transport isolation boundary
                    result = DeliveryResult(
                        DeliveryStatus.RETRY,
                        None,
                        str(exc),
                        error_code=DeliveryErrorCode.TRANSPORT_ERROR.value,
                    )
                if isinstance(result, bool):
                    result = DeliveryResult(
                        DeliveryStatus.ACCEPTED if result else DeliveryStatus.RETRY,
                        record.record_id if result else None,
                        error_code=None if result else DeliveryErrorCode.TRANSPORT_ERROR.value,
                    )
                state = repository.complete_attempt(record.record_id, result, attempted_at=started)
                remaining.remove(record.record_id)
                if state == "dead_letter":
                    self.logger.error("Telemetry record entered dead letter: %s", record.record_id)
        finally:
            repository.release_in_flight(remaining)
        return len(records)

    def _wait_for_rate_limit(self) -> None:
        if self.rate_limit_per_second <= 0:
            return
        now = time.monotonic()
        if self._last_send_started_at is not None:
            remaining = (1 / self.rate_limit_per_second) - (now - self._last_send_started_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_send_started_at = time.monotonic()

    def _run(self) -> None:
        while not self._stop.is_set():
            repository: SpoolRepository | None = None
            try:
                repository = self.repository.worker_connection()
                repository.set_metadata("worker_state", "running")
                repository.set_metadata("worker_last_error", "")
                last_checkpoint = time.monotonic()
                while not self._stop.is_set():
                    repository.set_metadata("worker_last_heartbeat_at", utc_text())
                    processed = self.deliver_once(repository)
                    if time.monotonic() - last_checkpoint >= self.checkpoint_interval_seconds:
                        repository.checkpoint()
                        last_checkpoint = time.monotonic()
                    if processed == 0:
                        self._wake.wait(1.0)
                        self._wake.clear()
            except Exception as exc:
                self.logger.exception("Telemetry delivery worker failed; restarting: %s", exc)
                with suppress(Exception):
                    if repository is not None:
                        repository.release_in_flight()
                        repository.set_metadata("worker_state", "error")
                        repository.set_metadata("worker_last_error", f"{type(exc).__name__}: {exc}")
                    else:
                        self.repository.set_metadata("worker_state", "error")
                        self.repository.set_metadata("worker_last_error", f"{type(exc).__name__}: {exc}")
                self._wake.wait(1.0)
                self._wake.clear()
            finally:
                if repository is not None:
                    repository.close()
