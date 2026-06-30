import json
import subprocess
import types

from src.sensors import network_health


def test_network_health_parses_nmcli_device_status() -> None:
    text = "lo:loopback:connected:lo\nwlan0:wifi:connected:WLAN16849707\n"

    assert network_health.parse_nmcli_device_status(text, "wlan0") == {
        "device": "wlan0",
        "type": "wifi",
        "state": "connected",
        "connection": "WLAN16849707",
    }
    assert network_health.parse_nmcli_device_status(text, "wlan1") is None


def test_network_health_parses_ip_and_gateway() -> None:
    ip_text = "2: wlan0    inet 192.168.1.42/24 brd 192.168.1.255 scope global wlan0\n"
    route_text = "default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.42 metric 600\n"

    assert network_health.parse_ip_addr_show(ip_text) == "192.168.1.42"
    assert network_health.parse_default_gateway(route_text) == "192.168.1.1"


def test_network_health_reads_wifi_profile_metrics(tmp_path) -> None:
    (tmp_path / "WLAN16849707.nmconnection").write_text("[connection]\n", encoding="utf-8")

    assert network_health.read_wifi_profile_metrics(str(tmp_path), "WLAN16849707", "WLAN16849707") == {
        "wifi_profile_count": 1,
        "active_profile_present": True,
        "preferred_profile_present": True,
    }


def test_network_health_reports_missing_profiles(tmp_path) -> None:
    assert network_health.read_wifi_profile_metrics(str(tmp_path), "WLAN16849707", "WLAN16849707") == {
        "wifi_profile_count": 0,
        "active_profile_present": False,
        "preferred_profile_present": False,
    }


def test_network_health_reads_recovery_status(tmp_path) -> None:
    status_file = tmp_path / "status.json"
    status_file.write_text(
        json.dumps(
            {
                "timestamp_utc": "2026-06-30T10:00:00Z",
                "action": "check_network",
                "result": "ok",
                "exit_code": 0,
            }
        ),
        encoding="utf-8",
    )

    assert network_health.read_recovery_status(str(status_file)) == {
        "last_recovery_exit_code": 0,
        "last_recovery_action": "check_network",
        "last_recovery_result": "ok",
        "last_recovery_at_utc": "2026-06-30T10:00:00Z",
    }


def test_network_health_keeps_partial_metrics_on_command_failure(monkeypatch, tmp_path) -> None:
    def fake_run(command, **_kwargs):
        if command[:3] == ["nmcli", "-t", "-f"]:
            return subprocess.CompletedProcess(command, 1, "", "nmcli unavailable")
        if command[:4] == ["ip", "route", "show", "default"]:
            return subprocess.CompletedProcess(command, 0, "default via 192.168.1.1 dev wlan0\n", "")
        if command[0] == "ping":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "unknown")

    monkeypatch.setattr(network_health.subprocess, "run", fake_run)
    monkeypatch.setattr(network_health.socket, "getaddrinfo", lambda *_args, **_kwargs: [types.SimpleNamespace()])

    reading = network_health.read(wifi_profile_dir=str(tmp_path))

    assert reading == {
        "default_gateway_reachable": True,
        "dns_resolution_ok": True,
        "internet_reachable": True,
        "wifi_profile_count": 0,
        "active_profile_present": False,
        "errors": [{"sensor": "network_interface", "message": "nmcli unavailable"}],
    }
