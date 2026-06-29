import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_status_page_fetches_status_data_branch() -> None:
    index = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "Live Status" in index
    assert "status-data/status/status.json" in index
    assert "STALE_AFTER_MINUTES = 15" in index
    assert "container" not in index.lower()


def test_status_sample_uses_public_contract_shape() -> None:
    sample = json.loads((ROOT / "status" / "status.sample.json").read_text(encoding="utf-8"))

    assert sample["schema_version"] == "senior-pomidor.status.v1"
    assert sample["overall_status"] in {"ok", "degraded", "stale", "unknown"}
    assert {"readiness", "services"} <= sample["core"].keys()
    assert {"service", "category", "state", "health", "exit_code", "status"} <= sample["core"]["services"][0].keys()
    assert {
        "device_id",
        "status",
        "last_telemetry_received_at",
        "minutes_since_telemetry",
        "health_alert_count",
    } <= sample["edge_devices"][0].keys()
