"""Application process and service health probes."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Any

from .base_sensor import round_metric

HealthValue = bool | float | int | str


def read(
    *,
    service_name: str | None = None,
    mock: bool = False,
) -> dict[str, Any]:
    if mock:
        mock_metrics: dict[str, HealthValue] = {
            "process_id": 1234,
            "process_running": True,
            "process_uptime_seconds": 3600,
        }
        if service_name:
            mock_metrics.update(
                {
                    "systemd_service_name": service_name,
                    "systemd_available": True,
                    "systemd_active_state": "active",
                    "systemd_sub_state": "running",
                    "systemd_main_pid": 1234,
                }
            )
        return mock_metrics

    metrics: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    _probe_metrics(metrics, errors, "application_process", read_process_metrics)
    if service_name:
        metrics.update(read_systemd_service_metrics(service_name))
    else:
        metrics["systemd_available"] = False

    if errors:
        metrics["errors"] = errors
    return metrics


def read_process_metrics() -> dict[str, HealthValue]:
    import psutil

    process = psutil.Process()
    return {
        "process_id": int(process.pid),
        "process_running": process.is_running(),
        "process_uptime_seconds": max(0, int(time.time() - process.create_time())),
        "process_memory_rss_bytes": int(process.memory_info().rss),
        "process_cpu_percent": round_metric(process.cpu_percent(interval=None), 1),
    }


def read_systemd_service_metrics(service_name: str) -> dict[str, HealthValue]:
    try:
        output = subprocess.run(
            [
                "systemctl",
                "show",
                service_name,
                "--property=ActiveState,SubState,MainPID",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except FileNotFoundError:
        return {
            "systemd_service_name": service_name,
            "systemd_available": False,
        }

    metrics: dict[str, HealthValue] = {
        "systemd_service_name": service_name,
        "systemd_available": output.returncode == 0,
    }
    if output.returncode != 0:
        return metrics

    properties = parse_systemctl_show(output.stdout)
    active_state = properties.get("ActiveState")
    sub_state = properties.get("SubState")
    main_pid = properties.get("MainPID")
    if active_state:
        metrics["systemd_active_state"] = active_state
        metrics["systemd_service_active"] = active_state == "active"
    if sub_state:
        metrics["systemd_sub_state"] = sub_state
    if main_pid and main_pid.isdigit():
        metrics["systemd_main_pid"] = int(main_pid)
    return metrics


def parse_systemctl_show(text: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def _probe_metrics(
    metrics: dict[str, Any],
    errors: list[dict[str, str]],
    sensor_name: str,
    reader: Callable[[], dict[str, HealthValue]],
) -> None:
    try:
        metrics.update(reader())
    except Exception as exc:  # noqa: BLE001 - per-probe health isolation
        errors.append({"sensor": sensor_name, "message": str(exc)})
