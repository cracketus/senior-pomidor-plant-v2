from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_page_links_to_live_dashboard() -> None:
    index = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "Live Dashboard" in index
    assert "Grafana Cloud" in index
    assert "https://stoutibex436.grafana.net/public-dashboards/913e33e032334becb3b01504373a364d" in index
    assert "container" not in index.lower()


def test_public_page_has_no_maintenance_or_dead_status_feed() -> None:
    index = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "maintenance" not in index.lower()
    assert "temporarily unavailable" not in index.lower()
    assert "status-data" not in index
