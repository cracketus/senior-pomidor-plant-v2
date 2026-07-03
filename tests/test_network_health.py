import json
import socket
import subprocess
import sys
import types

from src.sensors import network_health


def test_network_health_parses_nmcli_device_status() -> None:
    text = "lo:loopback:connected:lo\nwlan0:wifi:connected:example-wifi\n"

    assert network_health.parse_nmcli_device_status(text, "wlan0") == {
        "device": "wlan0",
        "type": "wifi",
        "state": "connected",
        "connection": "example-wifi",
    }
    assert network_health.parse_nmcli_device_status(text, "wlan1") is None


def test_network_health_parses_ip_and_gateway() -> None:
    ip_text = "2: wlan0    inet 192.0.2.42/24 brd 192.0.2.255 scope global wlan0\n"
    route_text = "default via 192.0.2.1 dev wlan0 proto dhcp src 192.0.2.42 metric 600\n"

    assert network_health.parse_ip_addr_show(ip_text) == "192.0.2.42"
    assert network_health.parse_default_gateway(route_text) == "192.0.2.1"


def test_network_health_parses_proc_net_default_route(tmp_path) -> None:
    route_file = tmp_path / "route"
    route_file.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010200C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n",
        encoding="utf-8",
    )

    assert network_health.read_default_route(route_file) == ("eth0", "192.0.2.1")


def test_network_health_uses_ethernet_fallback_when_nmcli_is_missing(monkeypatch) -> None:
    def raise_missing_nmcli(command, **_kwargs):
        if command[0] == "nmcli":
            raise FileNotFoundError(2, "No such file or directory", "nmcli")
        return subprocess.CompletedProcess(command, 127, "", "missing command")

    fake_psutil = types.SimpleNamespace(
        net_if_stats=lambda: {
            "eth0": types.SimpleNamespace(isup=True),
        },
        net_if_addrs=lambda: {
            "eth0": [
                types.SimpleNamespace(family=socket.AF_INET, address="192.0.2.42"),
            ],
        },
    )
    monkeypatch.setattr(network_health.subprocess, "run", raise_missing_nmcli)
    monkeypatch.setattr(network_health, "read_default_route", lambda: ("eth0", "192.0.2.1"))
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert network_health.read_interface_state("wlan0") == {
        "wifi_connected": False,
        "interface_up": True,
        "interface_name": "eth0",
        "interface_type": "ethernet",
        "ip_address": "192.0.2.42",
    }


def test_network_health_uses_wifi_fallback_when_nmcli_is_missing(monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        if command[0] == "nmcli":
            raise FileNotFoundError(2, "No such file or directory", "nmcli")
        if command[0] == "iwgetid":
            return subprocess.CompletedProcess(command, 0, "greenhouse\n", "")
        return subprocess.CompletedProcess(command, 127, "", "missing command")

    fake_psutil = types.SimpleNamespace(
        net_if_stats=lambda: {
            "wlan0": types.SimpleNamespace(isup=True),
        },
        net_if_addrs=lambda: {
            "wlan0": [
                types.SimpleNamespace(family=socket.AF_INET, address="192.0.2.43"),
            ],
        },
    )
    monkeypatch.setattr(network_health.subprocess, "run", fake_run)
    monkeypatch.setattr(network_health, "read_default_route", lambda: ("wlan0", "192.0.2.1"))
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert network_health.read_interface_state("wlan0") == {
        "wifi_connected": True,
        "interface_up": True,
        "interface_name": "wlan0",
        "interface_type": "wifi",
        "ip_address": "192.0.2.43",
        "ssid": "greenhouse",
    }


def test_network_health_parses_iwconfig_essid() -> None:
    assert network_health.parse_iwconfig_essid('wlan0     IEEE 802.11  ESSID:"greenhouse"') == "greenhouse"
    assert network_health.parse_iwconfig_essid('wlan0     IEEE 802.11  ESSID:"off/any"') is None


def test_network_health_reads_wifi_profile_metrics(tmp_path) -> None:
    (tmp_path / "example-wifi.nmconnection").write_text("[connection]\n", encoding="utf-8")

    assert network_health.read_wifi_profile_metrics(str(tmp_path), "example-wifi", "example-wifi") == {
        "wifi_profile_count": 1,
        "active_profile_present": True,
        "preferred_profile_present": True,
    }


def test_network_health_reports_missing_profiles(tmp_path) -> None:
    assert network_health.read_wifi_profile_metrics(str(tmp_path), "example-wifi", "example-wifi") == {
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
            return subprocess.CompletedProcess(command, 0, "default via 192.0.2.1 dev wlan0\n", "")
        if command[0] == "ping":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "unknown")

    monkeypatch.setattr(network_health.subprocess, "run", fake_run)
    monkeypatch.setattr(network_health.socket, "getaddrinfo", lambda *_args, **_kwargs: [types.SimpleNamespace()])
    fake_psutil = types.SimpleNamespace(
        net_if_stats=lambda: {
            "wlan0": types.SimpleNamespace(isup=False),
        },
        net_if_addrs=lambda: {
            "wlan0": [],
        },
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    reading = network_health.read(wifi_profile_dir=str(tmp_path))

    assert reading == {
        "wifi_connected": False,
        "interface_up": False,
        "interface_name": "wlan0",
        "interface_type": "wifi",
        "default_gateway_reachable": True,
        "dns_resolution_ok": True,
        "internet_reachable": True,
        "wifi_profile_count": 0,
        "active_profile_present": False,
    }
