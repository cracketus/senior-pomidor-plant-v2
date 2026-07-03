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


def test_network_health_reads_delivery_reachability(monkeypatch) -> None:
    calls = []

    def fake_tcp(host, port, timeout):
        calls.append((host, port, timeout))
        return host != "offline.local"

    monkeypatch.setattr(network_health, "_tcp_reachable", fake_tcp)

    assert network_health.read_delivery_reachability(
        mqtt_host="mqtt.local",
        mqtt_port=1883,
        http_enabled=True,
        core_http_url="https://core.local/api/v1/edge/telemetry",
        photo_upload_enabled=True,
        photo_upload_url="http://offline.local/api/v1/edge/photos",
        timeout_seconds=1.5,
    ) == {
        "mqtt_broker_reachable": True,
        "http_telemetry_reachable": True,
        "photo_upload_reachable": False,
    }
    assert calls == [
        ("mqtt.local", 1883, 1.5),
        ("core.local", 443, 1.5),
        ("offline.local", 80, 1.5),
    ]


def test_network_health_reads_queue_metrics(tmp_path) -> None:
    telemetry_dir = tmp_path / "telemetry"
    photo_dir = tmp_path / "photos"
    telemetry_dir.mkdir()
    photo_dir.mkdir()
    (telemetry_dir / "queued.json").write_text("{}", encoding="utf-8")
    (photo_dir / "pending.jpg").write_bytes(b"jpeg")
    (photo_dir / "pending.json").write_text(
        json.dumps({"file_name": "pending.jpg", "upload_status": "pending"}),
        encoding="utf-8",
    )
    (photo_dir / "uploaded.jpg").write_bytes(b"old")
    (photo_dir / "uploaded.json").write_text(
        json.dumps({"file_name": "uploaded.jpg", "upload_status": "uploaded"}),
        encoding="utf-8",
    )

    assert network_health.read_queue_metrics(str(telemetry_dir), str(photo_dir)) == {
        "telemetry_queue_file_count": 1,
        "telemetry_queue_size_bytes": 2,
        "photo_queue_file_count": 2,
        "photo_queue_size_bytes": len(b"jpeg")
        + len(json.dumps({"file_name": "pending.jpg", "upload_status": "pending"})),
    }


def test_network_health_reads_interface_counters(monkeypatch) -> None:
    fake_psutil = types.SimpleNamespace(
        net_io_counters=lambda pernic=True: {"wlan0": types.SimpleNamespace(errin=1, errout=2, dropin=3, dropout=4)}
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert network_health.read_interface_counters("wlan0") == {
        "interface_rx_error_count": 1,
        "interface_tx_error_count": 2,
        "interface_rx_drop_count": 3,
        "interface_tx_drop_count": 4,
    }


def test_network_health_reports_unavailable_interface_counters(monkeypatch) -> None:
    fake_psutil = types.SimpleNamespace(net_io_counters=lambda pernic=True: {})
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    try:
        network_health.read_interface_counters("wlan0")
    except RuntimeError as exc:
        assert str(exc) == "Network interface counters for wlan0 are unavailable"
    else:
        raise AssertionError("Expected unavailable interface counters to raise RuntimeError")


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
        net_io_counters=lambda pernic=True: {"wlan0": types.SimpleNamespace(errin=0, errout=0, dropin=0, dropout=0)},
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
        "telemetry_queue_file_count": 0,
        "telemetry_queue_size_bytes": 0,
        "photo_queue_file_count": 0,
        "photo_queue_size_bytes": 0,
        "interface_rx_error_count": 0,
        "interface_tx_error_count": 0,
        "interface_rx_drop_count": 0,
        "interface_tx_drop_count": 0,
    }
