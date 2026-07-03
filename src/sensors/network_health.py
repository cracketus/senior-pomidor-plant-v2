"""NetworkManager and edge network health probes."""

from __future__ import annotations

import json
import re
import socket
import struct
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

NetworkValue = bool | float | int | str


def read(
    *,
    wifi_interface: str = "wlan0",
    wifi_profile_dir: str = "/etc/NetworkManager/system-connections",
    wifi_preferred_profile: str | None = None,
    network_check_host: str = "1.1.1.1",
    network_dns_check_host: str = "example.com",
    recovery_status_file: str = "data/network-recovery/status.json",
    mqtt_host: str | None = None,
    mqtt_port: int = 1883,
    http_enabled: bool = False,
    core_http_url: str | None = None,
    photo_upload_enabled: bool = False,
    photo_upload_url: str | None = None,
    telemetry_queue_dir: str = "data/telemetry",
    photo_queue_dir: str = "data/photos",
    timeout_seconds: float = 2.0,
    mock: bool = False,
) -> dict[str, Any]:
    if mock:
        mock_metrics: dict[str, NetworkValue] = {
            "wifi_connected": True,
            "interface_up": True,
            "ssid": "example-wifi",
            "ip_address": "192.0.2.42",
            "default_gateway_reachable": True,
            "dns_resolution_ok": True,
            "internet_reachable": True,
            "wifi_profile_count": 1,
            "active_profile_present": True,
            "last_recovery_exit_code": 0,
            "mqtt_broker_reachable": True,
            "http_telemetry_reachable": True,
            "photo_upload_reachable": True,
            "telemetry_queue_file_count": 0,
            "telemetry_queue_size_bytes": 0,
            "photo_queue_file_count": 0,
            "photo_queue_size_bytes": 0,
            "interface_rx_error_count": 0,
            "interface_tx_error_count": 0,
            "interface_rx_drop_count": 0,
            "interface_tx_drop_count": 0,
        }
        if wifi_preferred_profile:
            mock_metrics["preferred_profile_present"] = True
        return mock_metrics

    metrics: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    _probe_metrics(metrics, errors, "network_interface", lambda: read_interface_state(wifi_interface))
    _probe_metrics(
        metrics,
        errors,
        "network_reachability",
        lambda: read_reachability(network_check_host, network_dns_check_host),
    )
    _probe_metrics(
        metrics,
        errors,
        "network_wifi_profiles",
        lambda: read_wifi_profile_metrics(wifi_profile_dir, metrics.get("ssid"), wifi_preferred_profile),
    )
    _probe_metrics(metrics, errors, "network_recovery_status", lambda: read_recovery_status(recovery_status_file))
    _probe_metrics(
        metrics,
        errors,
        "network_delivery_reachability",
        lambda: read_delivery_reachability(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            http_enabled=http_enabled,
            core_http_url=core_http_url,
            photo_upload_enabled=photo_upload_enabled,
            photo_upload_url=photo_upload_url,
            timeout_seconds=timeout_seconds,
        ),
    )
    _probe_metrics(
        metrics,
        errors,
        "network_delivery_queues",
        lambda: read_queue_metrics(telemetry_queue_dir, photo_queue_dir),
    )
    _probe_metrics(
        metrics,
        errors,
        "network_interface_counters",
        lambda: read_interface_counters(str(metrics.get("interface_name") or wifi_interface)),
    )

    if errors:
        metrics["errors"] = errors
    return metrics


def read_interface_state(interface: str) -> dict[str, NetworkValue]:
    try:
        output = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        device_status = parse_nmcli_device_status(output.stdout, interface)
    except RuntimeError:
        device_status = None

    if device_status is None:
        return read_interface_state_fallback(interface)

    resolved_interface = device_status["device"]
    interface_type = device_status["type"]
    connected = device_status["state"] == "connected"
    metrics: dict[str, NetworkValue] = {
        "wifi_connected": connected if interface_type == "wifi" else False,
        "interface_up": device_status["state"] not in {"unavailable", "unmanaged", "disconnected"},
        "interface_name": resolved_interface,
        "interface_type": interface_type,
    }
    connection = device_status.get("connection", "")
    if interface_type == "wifi" and connection and connection != "--":
        metrics["ssid"] = connection

    ip_address = read_ip_address(resolved_interface)
    if ip_address:
        metrics["ip_address"] = ip_address
    return metrics


def read_interface_state_fallback(preferred_interface: str) -> dict[str, NetworkValue]:
    import psutil

    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    route_interface, _gateway_ip = read_default_route()
    interface = _select_interface(preferred_interface, route_interface, stats)
    if not interface:
        raise RuntimeError(f"Network interface status for {preferred_interface} is unavailable")

    interface_stats = stats.get(interface)
    interface_type = "wifi" if interface.startswith(("wl", "wlan")) else "ethernet"
    metrics: dict[str, NetworkValue] = {
        "wifi_connected": bool(interface_stats and interface_stats.isup) if interface_type == "wifi" else False,
        "interface_up": bool(interface_stats and interface_stats.isup),
        "interface_name": interface,
        "interface_type": interface_type,
    }
    ip_address = _ip_address_from_psutil(addrs.get(interface, []))
    if ip_address:
        metrics["ip_address"] = ip_address
    if interface_type == "wifi":
        ssid = read_wifi_ssid(interface)
        if ssid:
            metrics["ssid"] = ssid
    return metrics


def parse_nmcli_device_status(text: str, interface: str) -> dict[str, str] | None:
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 3)
        if len(parts) != 4 or parts[0] != interface:
            continue
        return {
            "device": parts[0],
            "type": parts[1],
            "state": parts[2],
            "connection": parts[3],
        }
    return None


def read_ip_address(interface: str) -> str | None:
    output = _run(["ip", "-4", "-o", "addr", "show", "dev", interface], check=False)
    if output.returncode != 0:
        try:
            import psutil
        except ImportError:
            return None
        return _ip_address_from_psutil(psutil.net_if_addrs().get(interface, []))
    return parse_ip_addr_show(output.stdout)


def parse_ip_addr_show(text: str) -> str | None:
    parts = text.split()
    for index, part in enumerate(parts):
        if part == "inet" and index + 1 < len(parts):
            return parts[index + 1].split("/", 1)[0]
    return None


def read_reachability(network_check_host: str, network_dns_check_host: str) -> dict[str, NetworkValue]:
    gateway = _run(["ip", "route", "show", "default"], check=False)
    gateway_ip = parse_default_gateway(gateway.stdout) if gateway.returncode == 0 else None
    if gateway_ip is None:
        _route_interface, gateway_ip = read_default_route()

    metrics: dict[str, NetworkValue] = {
        "default_gateway_reachable": _host_reachable(gateway_ip) if gateway_ip else False,
        "dns_resolution_ok": _dns_resolves(network_dns_check_host),
        "internet_reachable": _host_reachable(network_check_host),
    }
    return metrics


def parse_default_gateway(text: str) -> str | None:
    parts = text.split()
    for index, part in enumerate(parts):
        if part == "via" and index + 1 < len(parts):
            return parts[index + 1]
    return None


def read_wifi_ssid(interface: str) -> str | None:
    output = _run(["iwgetid", "-r", interface], check=False)
    if output.returncode == 0 and output.stdout.strip():
        return output.stdout.strip()

    output = _run(["iwconfig", interface], check=False)
    if output.returncode == 0:
        return parse_iwconfig_essid(output.stdout + output.stderr)
    return None


def parse_iwconfig_essid(text: str) -> str | None:
    match = re.search(r'ESSID:"([^"]+)"', text)
    if not match:
        return None
    ssid = match.group(1)
    if not ssid or ssid.lower() == "off/any":
        return None
    return ssid


def read_default_route(route_path: Path = Path("/proc/net/route")) -> tuple[str | None, str | None]:
    try:
        lines = route_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None

    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        try:
            gateway = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
        except (OSError, ValueError, struct.error):
            gateway = None
        return fields[0], gateway
    return None, None


def read_wifi_profile_metrics(
    wifi_profile_dir: str,
    active_ssid: Any,
    preferred_profile: str | None,
) -> dict[str, NetworkValue]:
    profiles = list_wifi_profiles(Path(wifi_profile_dir))
    names = {profile.stem for profile in profiles}
    active_name = str(active_ssid) if isinstance(active_ssid, str) and active_ssid else None
    metrics: dict[str, NetworkValue] = {
        "wifi_profile_count": len(profiles),
        "active_profile_present": bool(active_name and active_name in names),
    }
    if preferred_profile:
        metrics["preferred_profile_present"] = preferred_profile in names
    return metrics


def list_wifi_profiles(profile_dir: Path) -> list[Path]:
    if not profile_dir.exists():
        raise RuntimeError(f"Wi-Fi profile directory is unavailable: {profile_dir}")
    if not profile_dir.is_dir():
        raise RuntimeError(f"Wi-Fi profile path is not a directory: {profile_dir}")
    return sorted(profile_dir.glob("*.nmconnection"))


def read_recovery_status(recovery_status_file: str) -> dict[str, NetworkValue]:
    path = Path(recovery_status_file)
    if not path.exists():
        return {}
    status = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(status, dict):
        raise RuntimeError(f"Recovery status file does not contain a JSON object: {path}")

    metrics: dict[str, NetworkValue] = {}
    exit_code = status.get("exit_code")
    if isinstance(exit_code, int):
        metrics["last_recovery_exit_code"] = exit_code
    for source_key, metric_key in [
        ("action", "last_recovery_action"),
        ("result", "last_recovery_result"),
        ("timestamp_utc", "last_recovery_at_utc"),
    ]:
        value = status.get(source_key)
        if isinstance(value, str) and value:
            metrics[metric_key] = value
    return metrics


def read_delivery_reachability(
    *,
    mqtt_host: str | None,
    mqtt_port: int,
    http_enabled: bool,
    core_http_url: str | None,
    photo_upload_enabled: bool,
    photo_upload_url: str | None,
    timeout_seconds: float,
) -> dict[str, NetworkValue]:
    metrics: dict[str, NetworkValue] = {}
    if mqtt_host:
        metrics["mqtt_broker_reachable"] = _tcp_reachable(mqtt_host, mqtt_port, timeout_seconds)
    if http_enabled and core_http_url:
        metrics["http_telemetry_reachable"] = _url_reachable(core_http_url, timeout_seconds)
    if photo_upload_enabled and photo_upload_url:
        metrics["photo_upload_reachable"] = _url_reachable(photo_upload_url, timeout_seconds)
    return metrics


def read_queue_metrics(telemetry_queue_dir: str, photo_queue_dir: str) -> dict[str, NetworkValue]:
    telemetry_files = _regular_files(Path(telemetry_queue_dir), "*.json")
    photo_files = _pending_photo_files(Path(photo_queue_dir))
    return {
        "telemetry_queue_file_count": len(telemetry_files),
        "telemetry_queue_size_bytes": sum(_safe_size(path) for path in telemetry_files),
        "photo_queue_file_count": len(photo_files),
        "photo_queue_size_bytes": sum(_safe_size(path) for path in photo_files),
    }


def read_interface_counters(interface: str) -> dict[str, NetworkValue]:
    import psutil

    counters = psutil.net_io_counters(pernic=True).get(interface)
    if counters is None:
        raise RuntimeError(f"Network interface counters for {interface} are unavailable")
    return {
        "interface_rx_error_count": int(counters.errin),
        "interface_tx_error_count": int(counters.errout),
        "interface_rx_drop_count": int(counters.dropin),
        "interface_tx_drop_count": int(counters.dropout),
    }


def _probe_metrics(
    metrics: dict[str, Any],
    errors: list[dict[str, str]],
    sensor_name: str,
    reader: Callable[[], dict[str, NetworkValue]],
) -> None:
    try:
        metrics.update(reader())
    except Exception as exc:  # noqa: BLE001 - per-probe health isolation
        errors.append({"sensor": sensor_name, "message": str(exc)})


def _ping(host: str | None) -> bool:
    if not host:
        return False
    output = _run(["ping", "-c", "1", "-W", "2", host], check=False)
    return output.returncode == 0


def _host_reachable(host: str | None) -> bool:
    if not host:
        return False
    if _ping(host):
        return True
    try:
        with socket.create_connection((host, 53), timeout=2):
            return True
    except OSError:
        return False


def _tcp_reachable(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _url_reachable(url: str, timeout_seconds: float) -> bool:
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return _tcp_reachable(parsed.hostname, port, timeout_seconds)


def _dns_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
    except OSError:
        return False
    return True


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        output = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except FileNotFoundError as exc:
        if check:
            raise RuntimeError(f"{command[0]} is unavailable") from exc
        return subprocess.CompletedProcess(command, 127, "", str(exc))
    if check and output.returncode != 0:
        message = (output.stderr or output.stdout).strip() or f"{command[0]} exited with {output.returncode}"
        raise RuntimeError(message)
    return output


def _select_interface(
    preferred_interface: str,
    route_interface: str | None,
    stats: dict[str, Any],
) -> str | None:
    preferred_stats = stats.get(preferred_interface)
    if preferred_stats and preferred_stats.isup:
        return preferred_interface
    if route_interface in stats:
        return route_interface
    if preferred_interface in stats:
        return preferred_interface
    for name, interface_stats in stats.items():
        if name != "lo" and interface_stats.isup:
            return name
    return None


def _ip_address_from_psutil(addresses: list[Any]) -> str | None:
    for address in addresses:
        if address.family == socket.AF_INET:
            return str(address.address)
    return None


def _regular_files(directory: Path, pattern: str) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob(pattern) if path.is_file())


def _pending_photo_files(directory: Path) -> list[Path]:
    pending: list[Path] = []
    for metadata_path in _regular_files(directory, "*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict) or metadata.get("upload_status") == "uploaded":
            continue
        image_path = directory / str(metadata.get("file_name") or metadata_path.with_suffix(".jpg").name)
        if image_path.exists():
            pending.extend([metadata_path, image_path])
    return pending


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
