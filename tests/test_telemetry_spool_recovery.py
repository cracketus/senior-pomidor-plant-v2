from src.telemetry_spool import (
    DeliveryErrorCode,
    DeliveryResult,
    DeliveryStatus,
    SpoolRepository,
)


def _payload() -> dict[str, object]:
    return {
        "schema_version": "senior-pomidor.edge.telemetry.v2",
        "device_id": "edge-01",
        "timestamp_utc": "2026-09-13T17:00:00Z",
        "pods": {},
        "system_health": {},
    }


def _repository(tmp_path) -> SpoolRepository:
    return SpoolRepository(
        tmp_path / "spool.sqlite3",
        boot_id="boot-a",
        retry_jitter=0,
        disk_warning_percent=101,
        disk_degraded_percent=102,
        disk_critical_percent=103,
    ).open()


def _claim(spool: SpoolRepository) -> str:
    claimed = spool.claim_batch()
    assert len(claimed) == 1
    return claimed[0].record_id


def test_duplicate_recovery_clears_current_ack_error_but_preserves_history(tmp_path) -> None:
    spool = _repository(tmp_path)
    record = spool.enqueue(_payload())
    _claim(spool)

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

    spool.connection.execute(
        "UPDATE records SET next_attempt_at=NULL WHERE record_id=?",
        (record.record_id,),
    )
    _claim(spool)

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
    _claim(spool)

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

    spool.connection.execute(
        "UPDATE records SET next_attempt_at=NULL WHERE record_id=?",
        (record.record_id,),
    )
    _claim(spool)

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


def test_restart_replay_success_clears_current_error_without_losing_attempt_history(tmp_path) -> None:
    spool = _repository(tmp_path)
    record = spool.enqueue(_payload())
    _claim(spool)
    assert (
        spool.complete_attempt(
            record.record_id,
            DeliveryResult(
                DeliveryStatus.RETRY,
                record.record_id,
                detail="temporary network failure",
                error_code=DeliveryErrorCode.TRANSPORT_ERROR.value,
            ),
            random_value=0.5,
        )
        == "pending"
    )
    spool.close()

    restarted = SpoolRepository(
        tmp_path / "spool.sqlite3",
        boot_id="boot-b",
        retry_jitter=0,
        disk_warning_percent=101,
        disk_degraded_percent=102,
        disk_critical_percent=103,
    ).open()
    restarted.connection.execute(
        "UPDATE records SET next_attempt_at=NULL WHERE record_id=?",
        (record.record_id,),
    )
    _claim(restarted)

    assert (
        restarted.complete_attempt(
            record.record_id,
            DeliveryResult(DeliveryStatus.ACCEPTED, record.record_id),
        )
        == "delivered"
    )

    health = restarted.health()
    assert health["last_error_code"] is None
    assert health["last_error_detail"] is None
    assert health["last_error_at_utc"] is None
    assert [attempt["result"] for attempt in restarted.attempts(record.record_id)] == ["retry", "accepted"]
