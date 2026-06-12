"""Raspberry Pi OS health probes."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base_sensor import round_metric

CPU_TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
WIRELESS_PATH = Path("/proc/net/wireless")


def read(
    wifi_interface: str = "wlan0",
    disk_usage_path: str = "/",
    mock: bool = False,
) -> dict[str, Any]:
    if mock:
        return {
            "cpu_temp_c": 56.4,
            "wifi_rssi_dbm": -68.0,
            "disk_usage_percent": 34.2,
            "io_wait_percent": 1.7,
        }

    metrics: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    _probe_metric(metrics, errors, "cpu_temp_c", "rpi_cpu_temp", lambda: read_cpu_temp_c())
    _probe_metric(metrics, errors, "wifi_rssi_dbm", "rpi_wifi_rssi", lambda: read_wifi_rssi_dbm(wifi_interface))
    _probe_metric(
        metrics, errors, "disk_usage_percent", "rpi_disk_usage", lambda: read_disk_usage_percent(disk_usage_path)
    )
    _probe_metric(metrics, errors, "io_wait_percent", "rpi_io_wait", read_io_wait_percent)

    if errors:
        metrics["errors"] = errors
    return metrics


def read_cpu_temp_c(path: Path = CPU_TEMP_PATH) -> float:
    raw = path.read_text(encoding="utf-8").strip()
    value = float(raw)
    if abs(value) > 1000:
        value /= 1000.0
    return round_metric(value, 1)


def read_wifi_rssi_dbm(interface: str, wireless_path: Path = WIRELESS_PATH) -> float:
    try:
        text = wireless_path.read_text(encoding="utf-8")
        rssi = parse_proc_net_wireless(text, interface)
        if rssi is not None:
            return rssi
    except FileNotFoundError:
        pass

    output = subprocess.run(
        ["iwconfig", interface],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    rssi = parse_iwconfig(output.stdout + output.stderr)
    if rssi is None:
        raise RuntimeError(f"RSSI for interface {interface} is unavailable")
    return rssi


def parse_proc_net_wireless(text: str, interface: str) -> float | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(f"{interface}:"):
            continue
        fields = stripped.split()
        if len(fields) < 4:
            return None
        try:
            return round_metric(float(fields[3].rstrip(".")), 0)
        except ValueError:
            return None
    return None


def parse_iwconfig(text: str) -> float | None:
    match = re.search(r"Signal level[=:\s]+(-?\d+(?:\.\d+)?)\s*dBm", text)
    if not match:
        return None
    return round_metric(float(match.group(1)), 0)


def read_disk_usage_percent(path: str) -> float:
    import psutil

    return round_metric(psutil.disk_usage(path).percent, 1)


def read_io_wait_percent() -> float:
    import psutil

    cpu_times = psutil.cpu_times_percent(interval=None)
    if not hasattr(cpu_times, "iowait"):
        raise RuntimeError("I/O wait is unavailable on this platform")
    return round_metric(cpu_times.iowait, 1)


def _probe_metric(
    metrics: dict[str, Any],
    errors: list[dict[str, str]],
    metric_name: str,
    sensor_name: str,
    reader: Callable[[], float],
) -> None:
    try:
        metrics[metric_name] = reader()
    except Exception as exc:  # noqa: BLE001 - per-probe health isolation
        errors.append({"sensor": sensor_name, "message": str(exc)})
