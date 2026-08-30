from src.edge_health import EDGE_HEALTH_SCHEMA_VERSION, EdgeHealthState, aggregate_edge_health, startup_edge_health


def healthy() -> dict:
    return {
        "rpi_core": {
            "filesystem_read_only": False,
            "recent_io_error_count": 0,
            "under_voltage_now": False,
            "throttled_now": False,
            "frequency_capped_now": False,
        },
        "network": {"http_telemetry_reachable": True},
        "application": {"process_running": True},
        "spool": {"status": "OK", "disk_status": "OK", "pending_count": 0},
        "watchdog": {"state": "healthy", "suppression": False},
    }


def test_healthy_sources_aggregate_to_versioned_ok() -> None:
    result = aggregate_edge_health(healthy())

    assert result.state is EdgeHealthState.OK
    assert result.as_dict() == {"schema_version": EDGE_HEALTH_SCHEMA_VERSION, "state": "OK", "reasons": []}


def test_startup_is_explicit_before_first_full_evaluation() -> None:
    assert startup_edge_health().as_dict()["state"] == "STARTUP"


def test_critical_rules_are_deterministic_and_beat_maintenance() -> None:
    value = healthy()
    value["spool"].update(status="CRITICAL", disk_status="CRITICAL")
    value["rpi_core"].update(filesystem_read_only=True, recent_io_error_count=2, under_voltage_now=True)
    value["watchdog"].update(state="maintenance", suppression=True, result="restart_failed")

    result = aggregate_edge_health(value)

    assert result.state is EdgeHealthState.CRITICAL
    assert result.reasons == (
        "spool.critical",
        "disk.critical",
        "filesystem.read_only",
        "filesystem.recent_io_errors",
        "power.under_voltage",
        "watchdog.recovery_suppressed",
        "watchdog.recovery_failed",
    )


def test_maintenance_beats_degraded_and_backlog() -> None:
    value = healthy()
    value["watchdog"]["state"] = "maintenance"
    value["spool"].update(status="DEGRADED", pending_count=3)

    assert aggregate_edge_health(value).state is EdgeHealthState.MAINTENANCE


def test_degraded_rules_beat_backlog() -> None:
    value = healthy()
    value["rpi_core"].update(throttled_now=True, frequency_capped_now=True)
    value["application"]["process_running"] = False
    value["watchdog"]["state"] = "cooldown"
    value["spool"].update(status="BACKLOG", pending_count=3)

    result = aggregate_edge_health(value)

    assert result.state is EdgeHealthState.DEGRADED
    assert result.reasons == (
        "watchdog.cooldown",
        "application.inactive",
        "power.throttled",
        "power.frequency_capped",
    )


def test_probe_errors_and_incomplete_data_are_degraded() -> None:
    value = healthy()
    value.pop("network")
    value["rpi_core"]["errors"] = [{"sensor": "disk", "message": "failed"}]

    result = aggregate_edge_health(value)

    assert result.state is EdgeHealthState.DEGRADED
    assert result.reasons == ("health.probe_errors", "health.data_incomplete")


def test_pending_or_unreachable_remote_delivery_is_backlog() -> None:
    value = healthy()
    value["spool"]["pending_count"] = 1
    value["network"]["http_telemetry_reachable"] = False

    result = aggregate_edge_health(value)

    assert result.state is EdgeHealthState.BACKLOG
    assert result.reasons == ("spool.backlog", "delivery.remote_unavailable")


def test_remote_outage_is_ignored_when_acquisition_is_suspended() -> None:
    value = healthy()
    value["network"]["http_telemetry_reachable"] = False

    assert aggregate_edge_health(value, acquisition_active=False).state is EdgeHealthState.OK


def test_absent_optional_watchdog_is_neutral() -> None:
    value = healthy()
    value["watchdog"] = {"configured": False}

    assert aggregate_edge_health(value).state is EdgeHealthState.OK


def test_configured_but_unavailable_watchdog_is_degraded() -> None:
    value = healthy()
    value["watchdog"] = {"state": "unavailable", "suppression": False, "configured": True}

    result = aggregate_edge_health(value)

    assert result.state is EdgeHealthState.DEGRADED
    assert result.reasons == ("watchdog.unavailable",)


def test_inaccessible_systemd_is_neutral_but_observed_inactive_service_is_degraded() -> None:
    value = healthy()
    value["application"].update(
        systemd_service_name="senior-pomidor-edge",
        systemd_available=False,
    )

    assert aggregate_edge_health(value).state is EdgeHealthState.OK

    value["application"].update(systemd_available=True, systemd_service_active=False)
    result = aggregate_edge_health(value)

    assert result.state is EdgeHealthState.DEGRADED
    assert result.reasons == ("systemd.inactive",)
