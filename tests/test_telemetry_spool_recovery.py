from src.telemetry_spool import (
    DeliveryErrorCode,
    DeliveryResult,
    DeliveryStatus,
    SpoolRepository,
)


def _payload(timestamp: str = "2026-09-13T17:00:00Z") -> dict[str, object]:
    return {
        "schema_version": "senior-pomidor.edge.telemetry.v2",
        "device_id": "edge-01",
        "timestamp_utc": timestamp,
        "pods": {},
        "system_health": {},
    }


def _repository(tmp_path, *, boot_id: str = "boot-a") -> SpoolRepository:
    return SpoolRepository(
        tmp_path / "spool.sqlite3",
        boot_id=boot_id,
        retry_jitter=0,
        disk_warning_percent=101,
        disk_degraded_percent=102,
        disk_critical_percent=103,
    ).open()


def _claim_one(spool: SpoolRepository) -> str:
    claimed = spool.claim_batch(1)
    assert len(claimed) == 1
    return claimed[0].record_id


def _make_retryable_now(spool: SpoolRepository, record_id: str) -> None:
    spool.connection.execute(
        "UPDATE records SET next_attempt_at=NULL WHERE record_id=?",
        (record_id,),
    )


def test_duplicate_recovery_clears_current_ack_error_but_preserves_history(tmp_path) -> None:
    spool = _repository(tmp_path)
    record = spool.enqueue(_payload())
    assert _claim_one(spool) == record.record_id

    assert (
        spool.complete_attempt(
            record.record_id,
            DeliveryResult(DeliveryStatus.ACCEPTED, "wrong-record-id"),
            random_value=0.5,
        )
        == "pending"
    )
    failed_health = spool.health()
    assert failed_health["last_error_code"] == DeliveryErrorCode.ACK_RECORD_ID_MISMATCH.value
    assert failed_health["last_error_detail"] == "missing or mismatched record_id in acknowledgement"
    assert failed_health["last_error_at_utc"] is not None

    _make_retryable_now(spool, record.record_id)
    assert _claim_one(spool) == record.record_id

    assert (
        spool.complete_attempt(
            record.record_id,
            DeliveryResult(DeliveryStatus.DUPLICATE, record.record_id),
        )
        == "delivered"
    )

    recovered_health = spool.health()
    assert recovered_health["last_delivery_result"] == DeliveryStatus.DUPLICATE.value
    assert recovered_health["last_error_code"] is None
    assert recovered_health["last_error_detail"] is None
    assert recovered_health["last_error_at_utc"] is None

    attempts = spool.attempts(record.record_id)
    assert [attempt["result"] for attempt in attempts] == ["retry", "duplicate"]
    assert attempts[0]["error_code"] == DeliveryErrorCode.ACK_RECORD_ID_MISMATCH.value
    assert attempts[1]["error_code"] is None


def test_accepted_recovery_clears_current_transport_error(tmp_path) -> None:
    spool = _repository(tmp_path)
    record = spool.enqueue(_payload())
    assert _claim_one(spool) == record.record_id

    assert (
        spool.complete_attempt(
            record.record_id,
            DeliveryResult(
                DeliveryStatus.RETRY,
                record.record_id,
                detail="connection reset",
                error_code=DeliveryErrorCode.TRANSPORT_ERROR.value,
            ),
            random_value=0.5,
        )
        == "pending"
    )
    assert spool.health()["last_error_code"] == DeliveryErrorCode.TRANSPORT_ERROR.value

    _make_retryable_now(spool, record.record_id)
    assert _claim_one(spool) == record.record_id

    assert (
        spool.complete_attempt(
            record.record_id,
            DeliveryResult(DeliveryStatus.ACCEPTED, record.record_id),
        )
        == "delivered"
    )

    health = spool.health()
    assert health["last_delivery_result"] == DeliveryStatus.ACCEPTED.value
    assert health["last_error_code"] is None
    assert health["last_error_detail"] is None
    assert health["last_error_at_utc"] is None


def test_successful_record_does_not_hide_another_unresolved_error(tmp_path) -> None:
    spool = _repository(tmp_path)
    failed = spool.enqueue(_payload("2026-09-13T17:00:00Z"))
    successful = spool.enqueue(_payload("2026-09-13T17:01:00Z"))

    # Claim only the older record first and leave it pending with a transport error.
    assert _claim_one(spool) == successful.record_id
    assert (
        spool.complete_attempt(
            successful.record_id,
            DeliveryResult(
                DeliveryStatus.RETRY,
                successful.record_id,
                detail="connection reset",
                error_code=DeliveryErrorCode.TRANSPORT_ERROR.value,
            ),
            random_value=0.5,
        )
        == "pending"
    )

    # Deliver the other record successfully. Its success must not clear the
    # unresolved error belonging to the still-pending record.
    assert _claim_one(spool) == failed.record_id
    assert (
        spool.complete_attempt(
            failed.record_id,
            DeliveryResult(DeliveryStatus.ACCEPTED, failed.record_id),
        )
        == "delivered"
    )

    health = spool.health()
    assert health["pending_count"] == 1
    assert health["last_delivery_result"] == DeliveryStatus.ACCEPTED.value
    assert health["last_error_code"] == DeliveryErrorCode.TRANSPORT_ERROR.value
    assert health["last_error_detail"] == "connection reset"
    assert health["last_error_at_utc"] is not None


def test_lost_ack_restart_recovers_in_flight_and_duplicate_completes_delivery(tmp_path) -> None:
    spool = _repository(tmp_path)
    record = spool.enqueue(_payload())
    assert _claim_one(spool) == record.record_id

    # The HTTP request has started and Core may already have persisted the
    # record, but Edge crashes before the acknowledgement is recorded locally.
    spool.start_attempt(record.record_id)
    assert spool.get(record.record_id).state == "in_flight"
    assert spool.attempts(record.record_id) == []
    spool.close()

    restarted = _repository(tmp_path, boot_id="boot-b")
    recovered = restarted.get(record.record_id)
    assert recovered.state == "pending"
    recovery_count = restarted.connection.execute(
        "SELECT recovery_count FROM records WHERE record_id=?",
        (record.record_id,),
    ).fetchone()[0]
    assert recovery_count == 1

    assert _claim_one(restarted) == record.record_id
    assert (
        restarted.complete_attempt(
            record.record_id,
            DeliveryResult(DeliveryStatus.DUPLICATE, record.record_id),
        )
        == "delivered"
    )

    health = restarted.health()
    assert health["pending_count"] == 0
    assert health["last_delivery_result"] == DeliveryStatus.DUPLICATE.value
    assert health["last_error_code"] is None
    assert health["last_error_detail"] is None
    assert health["last_error_at_utc"] is None
    assert health["replayed_total"] == 1

    attempts = restarted.attempts(record.record_id)
    assert [attempt["result"] for attempt in attempts] == ["duplicate"]
    assert attempts[0]["error_code"] is None
