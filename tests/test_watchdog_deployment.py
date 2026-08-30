from __future__ import annotations

from pathlib import Path

from scripts import edge_watchdog

ROOT = Path(__file__).resolve().parents[1]


def test_watchdog_systemd_unit_is_notify_supervised_and_can_manage_edge_service() -> None:
    unit = (ROOT / "deploy/systemd/senior-pomidor-watchdog.service").read_text(encoding="utf-8")
    assert "Type=notify" in unit
    assert "WatchdogSec=" in unit
    assert "scripts/edge_watchdog.py" in unit
    assert "ReadWritePaths=/opt/senior-pomidor-plant-v2/data" in unit


def test_edge_service_restart_uses_prebuilt_image() -> None:
    unit = (ROOT / "deploy/systemd/senior-pomidor-edge.service").read_text(encoding="utf-8")
    assert "docker compose build" not in unit
    assert "ExecStart=/usr/bin/docker compose up --no-build senior-pomidor-edge" in unit

    setup = (ROOT / "scripts/setup_raspberry_pi.sh").read_text(encoding="utf-8")
    assert '"${SUDO[@]}" docker compose build senior-pomidor-edge' in setup

    guide = (ROOT / "docs/edge-service.md").read_text(encoding="utf-8")
    install_build = guide.index("sudo docker compose build senior-pomidor-edge")
    install_start = guide.index("sudo systemctl enable --now senior-pomidor-edge.service")
    update_section = guide.index("## Update")
    update_build = guide.index("sudo docker compose build senior-pomidor-edge", update_section)
    update_restart = guide.index("sudo systemctl restart senior-pomidor-edge.service", update_section)
    assert install_build < install_start
    assert update_build < update_restart


def test_hardware_setup_installs_watchdog_by_default_with_explicit_opt_out() -> None:
    setup = (ROOT / "scripts/setup_raspberry_pi.sh").read_text(encoding="utf-8")
    assert "--install-watchdog)" in setup
    assert "--no-watchdog)" in setup
    assert 'if [ "$INSTALL_WATCHDOG" = "default" ]; then' in setup
    assert 'if [ "$MODE" = "hardware" ]; then' in setup
    assert '[ "$INSTALL_WATCHDOG" = "true" ] || return' in setup
    assert "--install-watchdog is supported only with --hardware" in setup
    assert "RuntimeWatchdogSec=30s" in setup
    assert 'ensure_line "$config_file" "dtparam=watchdog=on"' in setup
    assert 'grep -qi "Raspberry Pi" /proc/device-tree/model' in setup
    assert "systemctl enable senior-pomidor-edge.service senior-pomidor-watchdog.service" in setup


def test_edge_and_watchdog_units_use_checkout_env_only() -> None:
    edge = (ROOT / "deploy/systemd/senior-pomidor-edge.service").read_text(encoding="utf-8")
    watchdog = (ROOT / "deploy/systemd/senior-pomidor-watchdog.service").read_text(encoding="utf-8")

    expected = "EnvironmentFile=-/opt/senior-pomidor-plant-v2/.env"
    assert expected in edge
    assert expected in watchdog
    assert "/etc/senior-pomidor/edge.env" not in edge


def test_canonical_container_has_no_host_supervisor_control_socket() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "/var/run/docker.sock" not in compose
    assert "/run/docker.sock" not in compose
    assert "/run/dbus" not in compose


def test_setup_prepares_operator_owned_maintenance_directory_before_enabling_services() -> None:
    setup = (ROOT / "scripts/setup_raspberry_pi.sh").read_text(encoding="utf-8")
    prepare = setup.index('install -d -o "$TARGET_USER" -g "$target_group" -m 0750 "${repo_dir}/data/watchdog"')
    enable = setup.index("systemctl enable senior-pomidor-edge.service senior-pomidor-watchdog.service")
    marker = setup.index('"${repo_dir}/data/watchdog/installed"')

    assert 'target_group="$(id -gn "$TARGET_USER")"' in setup
    assert prepare < enable
    assert prepare < marker < enable


def test_watchdog_cli_reports_invalid_configuration_without_traceback(monkeypatch, caplog) -> None:
    def invalid_config():
        raise ValueError("WATCHDOG_TIMEOUT_SECONDS must be greater than POLL_INTERVAL_SECONDS")

    monkeypatch.setattr(edge_watchdog.WatchdogConfig, "from_env", invalid_config)
    monkeypatch.setattr("sys.argv", ["edge_watchdog.py", "--once"])

    assert edge_watchdog.main() == 2
    assert "Invalid watchdog configuration" in caplog.text
