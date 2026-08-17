from src.telemetry_spool import DeliveryResult, DeliveryStatus, DeliveryWorker, SpoolRepository


def payload(index: int) -> dict[str, object]:
    return {
        "schema_version": "senior-pomidor.edge.telemetry.v2",
        "device_id": "edge-outage",
        "timestamp_utc": f"2026-08-16T10:{index:02d}:00Z",
        "pods": {},
        "system_health": {},
    }


def open_spool(path, boot_id: str) -> SpoolRepository:
    return SpoolRepository(
        path,
        boot_id=boot_id,
        retry_jitter=0,
        disk_warning_percent=101,
        disk_degraded_percent=102,
        disk_critical_percent=103,
    ).open()


def test_outage_restart_lost_ack_mixed_replay_and_second_outage(tmp_path) -> None:
    path = tmp_path / "spool.sqlite3"
    spool = open_spool(path, "boot-a")
    records = [spool.enqueue(payload(index)) for index in range(4)]
    observed = {record.record_id: record.observed_at for record in records}

    outage = DeliveryWorker(spool, RetrySender(), NullMqttSender(), batch_size=4)
    assert outage.deliver_once() == 4
    assert spool.health()["failure_total"] == 4
    spool.connection.execute("UPDATE records SET next_attempt_at=NULL")

    recovered_id = spool.claim_batch(1)[0].record_id
    spool.close()
    spool = open_spool(path, "boot-b")
    assert spool.get(recovered_id).state == "pending"

    results = {
        records[0].record_id: DeliveryStatus.ACCEPTED,
        records[1].record_id: DeliveryStatus.REJECTED,
        records[2].record_id: DeliveryStatus.ACCEPTED,
        recovered_id: DeliveryStatus.DUPLICATE,
    }
    replay = DeliveryWorker(spool, MappingSender(results), NullMqttSender(), batch_size=4)
    assert replay.deliver_once() == 4
    assert spool.get(records[1].record_id).state == "dead_letter"
    assert spool.health()["replayed_total"] == 3

    second_outage = spool.enqueue(payload(10))
    assert DeliveryWorker(spool, RetrySender(), NullMqttSender(), batch_size=1).deliver_once() == 1
    fresh = spool.enqueue(payload(11))
    spool.connection.execute("UPDATE records SET next_attempt_at=NULL")
    capture = CapturingSender()
    assert DeliveryWorker(spool, capture, NullMqttSender(), batch_size=2).deliver_once() == 2

    assert capture.record_ids == [fresh.record_id, second_outage.record_id]
    assert all(spool.get(record.record_id).observed_at == observed[record.record_id] for record in records)
    assert spool.get(records[1].record_id).state == "dead_letter"
    assert spool.health()["pending_count"] == 0
    assert spool.health()["in_flight_count"] == 0


class RetrySender:
    def send(self, payload):
        return DeliveryResult(DeliveryStatus.RETRY, payload["record_id"], error_code="transport_error")


class MappingSender:
    def __init__(self, results):
        self.results = results

    def send(self, payload):
        return DeliveryResult(self.results[payload["record_id"]], payload["record_id"])


class CapturingSender:
    def __init__(self) -> None:
        self.record_ids = []

    def send(self, payload):
        self.record_ids.append(payload["record_id"])
        return DeliveryResult(DeliveryStatus.ACCEPTED, payload["record_id"])


class NullMqttSender:
    def publish(self, _payload):
        return True
