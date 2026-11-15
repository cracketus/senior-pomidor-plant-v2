from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_page_does_not_link_to_grafana() -> None:
    index = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "grafana.net" not in index
    assert "container" not in index.lower()


def test_public_page_has_no_maintenance_or_dead_status_feed() -> None:
    index = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "maintenance" not in index.lower()
    assert "temporarily unavailable" not in index.lower()
    assert "status-data" not in index
