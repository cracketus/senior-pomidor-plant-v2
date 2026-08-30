"""Canonical aggregation of edge-node health signals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

EDGE_HEALTH_SCHEMA_VERSION = "senior-pomidor.edge.health.v1"


class EdgeHealthState(StrEnum):
    OK = "OK"
    BACKLOG = "BACKLOG"
    DEGRADED = "DEGRADED"
    MAINTENANCE = "MAINTENANCE"
    CRITICAL = "CRITICAL"
    STARTUP = "STARTUP"


@dataclass(frozen=True)
class EdgeHealth:
    state: EdgeHealthState
    reasons: tuple[str, ...] = ()
    schema_version: str = EDGE_HEALTH_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "reasons": list(self.reasons),
        }


def aggregate_edge_health(
    system_health: object,
    *,
    remote_delivery_configured: bool = True,
    acquisition_active: bool = True,
) -> EdgeHealth:
    """Aggregate explicit probe signals without inventing resource thresholds."""
    if not isinstance(system_health, dict):
        return EdgeHealth(EdgeHealthState.DEGRADED, ("health.data_incomplete",))

    health: dict[str, Any] = system_health
    rpi = _mapping(health.get("rpi_core"))
    network = _mapping(health.get("network"))
    application = _mapping(health.get("application"))
    spool = _mapping(health.get("spool"))
    watchdog = _mapping(health.get("watchdog"))
    watchdog_configured = watchdog.get("configured") is not False

    critical: list[str] = []
    if spool.get("status") == "CRITICAL":
        critical.append("spool.critical")
    if spool.get("disk_status") == "CRITICAL":
        critical.append("disk.critical")
    if rpi.get("filesystem_read_only") is True:
        critical.append("filesystem.read_only")
    if _positive(rpi.get("recent_io_error_count")):
        critical.append("filesystem.recent_io_errors")
    if rpi.get("under_voltage_now") is True:
        critical.append("power.under_voltage")
    watchdog_state = str(watchdog.get("state", "unknown")).lower()
    watchdog_result = str(watchdog.get("result", "")).lower()
    if watchdog_configured and (watchdog.get("suppression") is True or watchdog_state == "suppressed"):
        critical.append("watchdog.recovery_suppressed")
    if watchdog_configured and watchdog_state == "recovering":
        critical.append("watchdog.recovery_active")
    if watchdog_configured and watchdog_result.endswith("_failed"):
        critical.append("watchdog.recovery_failed")
    if critical:
        return EdgeHealth(EdgeHealthState.CRITICAL, tuple(critical))

    if watchdog_configured and watchdog_state == "maintenance":
        return EdgeHealth(EdgeHealthState.MAINTENANCE, ("watchdog.maintenance",))

    degraded: list[str] = []
    if spool.get("status") == "DEGRADED" or spool.get("disk_status") == "DEGRADED":
        degraded.append("spool.degraded")
    if watchdog_configured and watchdog_state in {"unavailable", "unknown", "starting", "cooldown"}:
        degraded.append(f"watchdog.{watchdog_state}")
    if application.get("process_running") is False:
        degraded.append("application.inactive")
    if (
        application.get("systemd_service_name")
        and application.get("systemd_available") is True
        and application.get("systemd_service_active") is False
    ):
        degraded.append("systemd.inactive")
    if rpi.get("throttled_now") is True:
        degraded.append("power.throttled")
    if rpi.get("frequency_capped_now") is True:
        degraded.append("power.frequency_capped")
    if _has_probe_errors(health):
        degraded.append("health.probe_errors")
    if not _required_data_present(rpi, network, application, spool, watchdog):
        degraded.append("health.data_incomplete")
    if degraded:
        return EdgeHealth(EdgeHealthState.DEGRADED, tuple(dict.fromkeys(degraded)))

    backlog: list[str] = []
    if (
        spool.get("status") == "BACKLOG"
        or _positive(spool.get("backlog_count"))
        or _positive(spool.get("pending_count"))
        or _positive(spool.get("in_flight_count"))
    ):
        backlog.append("spool.backlog")
    if remote_delivery_configured and acquisition_active and network.get("http_telemetry_reachable") is False:
        backlog.append("delivery.remote_unavailable")
    if backlog:
        return EdgeHealth(EdgeHealthState.BACKLOG, tuple(backlog))
    return EdgeHealth(EdgeHealthState.OK)


def startup_edge_health() -> EdgeHealth:
    return EdgeHealth(EdgeHealthState.STARTUP, ("collector.starting",))


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _positive(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _has_probe_errors(value: object) -> bool:
    if isinstance(value, dict):
        if isinstance(value.get("error"), dict):
            return True
        errors = value.get("errors")
        if isinstance(errors, list) and bool(errors):
            return True
        return any(_has_probe_errors(item) for key, item in value.items() if key not in {"error", "errors"})
    if isinstance(value, list):
        return any(_has_probe_errors(item) for item in value)
    return False


def _required_data_present(
    rpi: dict[str, Any],
    network: dict[str, Any],
    application: dict[str, Any],
    spool: dict[str, Any],
    watchdog: dict[str, Any],
) -> bool:
    base_present = all(
        (
            all(key in rpi for key in ("filesystem_read_only", "recent_io_error_count", "under_voltage_now")),
            bool(network),
            "process_running" in application,
            all(key in spool for key in ("status", "disk_status")),
            watchdog.get("configured") is False or "state" in watchdog,
        )
    )
    if application.get("systemd_service_name"):
        return (
            base_present
            and "systemd_available" in application
            and (
                application.get("systemd_available") is not True
                or "systemd_service_active" in application
                or "systemd_active_state" in application
            )
        )
    return base_present
