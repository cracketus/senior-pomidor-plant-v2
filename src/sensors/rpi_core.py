"""Raspberry Pi OS health probes."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base_sensor import round_metric

CPU_TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
WIRELESS_PATH = Path("/proc/net/wireless")
IO_ERROR_PATTERN = re.compile(
    r"(?:I/O error|Buffer I/O error|EXT[234]-fs error|mmc.*\berror\b|"
    r"read-only file system|blk_update_request|end_request.*I/O)",
    re.IGNORECASE,
)

MetricValue = bool | float | int


def read(
    wifi_interface: str = "wlan0",
    disk_usage_path: str = "/",
    telemetry_buffer_path: str = "data/telemetry",
    photo_buffer_path: str = "data/photos",
    mock: bool = False,
) -> dict[str, Any]:
    if mock:
        return {
            "cpu_temp_c": 56.4,
            "wifi_rssi_dbm": -68.0,
            "disk_usage_percent": 34.2,
            "disk_free_percent": 65.8,
            "disk_total_bytes": 32_000_000_000,
            "disk_used_bytes": 10_944_000_000,
            "disk_free_bytes": 21_056_000_000,
            "filesystem_read_only": False,
            "telemetry_buffer_file_count": 3,
            "telemetry_buffer_size_bytes": 12_288,
            "photo_buffer_file_count": 2,
            "photo_buffer_size_bytes": 2_400_000,
            "recent_io_error_count": 0,
            "io_wait_percent": 1.7,
            "under_voltage_now": False,
            "frequency_capped_now": False,
            "throttled_now": False,
            "under_voltage_seen": False,
            "frequency_capped_seen": False,
            "throttled_seen": False,
            "memory_usage_percent": 42.5,
            "memory_available_bytes": 512_000_000,
            "swap_usage_percent": 3.1,
            "swap_available_bytes": 248_000_000,
            "load_average_1m": 0.42,
            "load_average_5m": 0.35,
            "load_average_15m": 0.28,
            "uptime_seconds": 86_400,
        }

    metrics: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    _probe_metric(metrics, errors, "cpu_temp_c", "rpi_cpu_temp", lambda: read_cpu_temp_c())
    _probe_metric(metrics, errors, "wifi_rssi_dbm", "rpi_wifi_rssi", lambda: read_wifi_rssi_dbm(wifi_interface))
    _probe_metrics(metrics, errors, "rpi_disk_usage", lambda: read_disk_usage(disk_usage_path))
    _probe_metric(
        metrics,
        errors,
        "filesystem_read_only",
        "rpi_filesystem_status",
        lambda: read_filesystem_read_only(disk_usage_path),
    )
    _probe_metrics(
        metrics,
        errors,
        "rpi_telemetry_buffer",
        lambda: read_buffer_metrics(telemetry_buffer_path, "telemetry_buffer"),
    )
    _probe_metrics(
        metrics,
        errors,
        "rpi_photo_buffer",
        lambda: read_buffer_metrics(photo_buffer_path, "photo_buffer"),
    )
    _probe_metric(metrics, errors, "recent_io_error_count", "rpi_recent_io_errors", read_recent_io_error_count)
    _probe_metric(metrics, errors, "io_wait_percent", "rpi_io_wait", read_io_wait_percent)
    _probe_metrics(metrics, errors, "rpi_throttling", read_throttling_metrics)
    _probe_metrics(metrics, errors, "rpi_memory", read_memory_metrics)
    _probe_metrics(metrics, errors, "rpi_load_average", read_load_average_metrics)
    _probe_metric(metrics, errors, "uptime_seconds", "rpi_uptime", read_uptime_seconds)

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


def read_disk_usage(path: str) -> dict[str, MetricValue]:
    import psutil

    usage = psutil.disk_usage(path)
    free_percent = (usage.free / usage.total * 100.0) if usage.total else 0.0
    return {
        "disk_usage_percent": round_metric(usage.percent, 1),
        "disk_free_percent": round_metric(free_percent, 1),
        "disk_total_bytes": int(usage.total),
        "disk_used_bytes": int(usage.used),
        "disk_free_bytes": int(usage.free),
    }


def read_disk_usage_percent(path: str) -> float:
    return float(read_disk_usage(path)["disk_usage_percent"])


def read_filesystem_read_only(path: str) -> bool:
    import psutil

    target = Path(path).resolve(strict=False)
    matching_partitions = [
        partition
        for partition in psutil.disk_partitions(all=True)
        if _path_is_within(target, Path(partition.mountpoint).resolve(strict=False))
    ]
    if not matching_partitions:
        raise RuntimeError(f"Filesystem mount for {path} is unavailable")

    partition = max(matching_partitions, key=lambda item: len(Path(item.mountpoint).parts))
    options = {option.strip().lower() for option in partition.opts.split(",")}
    if "ro" in options:
        return True
    if "rw" in options:
        return False
    raise RuntimeError(f"Filesystem mount options for {partition.mountpoint} do not include ro or rw")


def read_buffer_metrics(path: str, metric_prefix: str) -> dict[str, MetricValue]:
    buffer_path = Path(path)
    if not buffer_path.exists():
        return {
            f"{metric_prefix}_file_count": 0,
            f"{metric_prefix}_size_bytes": 0,
        }

    file_count = 0
    size_bytes = 0
    for entry in buffer_path.rglob("*"):
        if not entry.is_file():
            continue
        try:
            size_bytes += entry.stat().st_size
        except FileNotFoundError:
            continue
        file_count += 1
    return {
        f"{metric_prefix}_file_count": file_count,
        f"{metric_prefix}_size_bytes": size_bytes,
    }


def read_recent_io_error_count() -> int:
    try:
        output = subprocess.run(
            ["journalctl", "--dmesg", "--since", "-1 hour", "--no-pager", "--output=cat"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except FileNotFoundError:
        return 0
    if output.returncode != 0:
        message = (output.stderr or output.stdout).strip() or f"journalctl exited with {output.returncode}"
        raise RuntimeError(f"Kernel I/O error log is unavailable: {message}")
    return sum(1 for line in output.stdout.splitlines() if IO_ERROR_PATTERN.search(line))


def read_io_wait_percent() -> float:
    import psutil

    cpu_times = psutil.cpu_times_percent(interval=None)
    if not hasattr(cpu_times, "iowait"):
        raise RuntimeError("I/O wait is unavailable on this platform")
    return round_metric(cpu_times.iowait, 1)


def read_throttling_metrics() -> dict[str, MetricValue]:
    try:
        output = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("vcgencmd is unavailable") from exc
    if output.returncode != 0:
        message = (output.stderr or output.stdout).strip() or f"vcgencmd exited with {output.returncode}"
        raise RuntimeError(f"Raspberry Pi throttling status is unavailable: {message}")
    return parse_throttled_flags(output.stdout)


def parse_throttled_flags(text: str) -> dict[str, MetricValue]:
    match = re.search(r"throttled=([0-9a-fA-Fx]+)", text)
    if not match:
        raise RuntimeError(f"Unexpected vcgencmd get_throttled output: {text.strip()}")
    flags = int(match.group(1), 0)
    return {
        "under_voltage_now": bool(flags & (1 << 0)),
        "frequency_capped_now": bool(flags & (1 << 1)),
        "throttled_now": bool(flags & (1 << 2)),
        "under_voltage_seen": bool(flags & (1 << 16)),
        "frequency_capped_seen": bool(flags & (1 << 17)),
        "throttled_seen": bool(flags & (1 << 18)),
    }


def read_memory_metrics() -> dict[str, MetricValue]:
    import psutil

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "memory_usage_percent": round_metric(memory.percent, 1),
        "memory_available_bytes": int(memory.available),
        "swap_usage_percent": round_metric(swap.percent, 1),
        "swap_available_bytes": int(swap.free),
    }


def read_load_average_metrics() -> dict[str, MetricValue]:
    try:
        import psutil

        load_1m, load_5m, load_15m = psutil.getloadavg()
    except (AttributeError, OSError) as exc:
        raise RuntimeError("Load average is unavailable on this platform") from exc
    return {
        "load_average_1m": round_metric(load_1m, 2),
        "load_average_5m": round_metric(load_5m, 2),
        "load_average_15m": round_metric(load_15m, 2),
    }


def read_uptime_seconds() -> int:
    try:
        import psutil

        return max(0, int(time.time() - psutil.boot_time()))
    except (AttributeError, OSError) as exc:
        raise RuntimeError("System uptime is unavailable on this platform") from exc


def _probe_metric(
    metrics: dict[str, Any],
    errors: list[dict[str, str]],
    metric_name: str,
    sensor_name: str,
    reader: Callable[[], MetricValue],
) -> None:
    try:
        metrics[metric_name] = reader()
    except Exception as exc:  # noqa: BLE001 - per-probe health isolation
        errors.append({"sensor": sensor_name, "message": str(exc)})


def _probe_metrics(
    metrics: dict[str, Any],
    errors: list[dict[str, str]],
    sensor_name: str,
    reader: Callable[[], dict[str, MetricValue]],
) -> None:
    try:
        metrics.update(reader())
    except Exception as exc:  # noqa: BLE001 - per-probe health isolation
        errors.append({"sensor": sensor_name, "message": str(exc)})


def _path_is_within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents
