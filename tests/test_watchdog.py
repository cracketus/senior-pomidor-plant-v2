from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from src.telemetry_spool import SpoolRepository
from src.watchdog import (
    WATCHDOG_INSTALLATION_MARKER,
    HeartbeatWriter,
    HostWatchdog,
    WatchdogConfig,
    atomic_write_json,
    read_json,
    read_watchdog_health,
    set_maintenance_hold,
    utc_text,
)


def test_watchdog_health_distinguishes_unconfigured_from_stale_status(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 12, 0, tzinfo=UTC))
    status_path = tmp_path / "status.json"

    assert read_watchdog_health(status_path, max_age_seconds=30, now=clock) == {
        "state": "unavailable",
        "suppression": False,
        "configured": False,
    }

    (status_path.parent / WATCHDOG_INSTALLATION_MARKER).touch()
    assert read_watchdog_health(status_path, max_age_seconds=30, now=clock) == {
        "state": "unavailable",
        "reason": "status_missing",
        "suppression": False,
        "configured": True,
    }

    (status_path.parent / WATCHDOG_INSTALLATION_MARKER).unlink()
    atomic_write_json(
        status_path,
        {
            "watchdog_state": "healthy",
            "updated_at_utc": utc_text(clock() - timedelta(seconds=31)),
            "suppression": True,
        },
    )

    assert read_watchdog_health(status_path, max_age_seconds=30, now=clock) == {
        "state": "unavailable",
        "reason": "status_stale",
        "suppression": False,
        "configured": True,
    }


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-08-19T12:00:01Z"])
def test_watchdog_health_rejects_invalid_or_future_status_timestamps(tmp_path, timestamp) -> None:
    clock = Clock(datetime(2026, 8, 19, 12, 0, tzinfo=UTC))
    status_path = tmp_path / "status.json"
    atomic_write_json(status_path, {"watchdog_state": "healthy", "updated_at_utc": timestamp})

    result = read_watchdog_health(status_path, max_age_seconds=30, now=clock)

    assert result["state"] == "unavailable"
    assert result["reason"] == "status_timestamp_invalid"
    assert result["configured"] is True


@pytest.mark.parametrize("raw", ["not-json", "[]", "{}"])
def test_watchdog_health_rejects_malformed_status(tmp_path, raw) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(raw, encoding="utf-8")

    result = read_watchdog_health(status_path)

    assert result == {
        "state": "unavailable",
        "reason": "status_malformed",
        "suppression": False,
        "configured": True,
    }


def test_watchdog_health_reports_unreadable_status(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "status.json"
    status_path.touch()
    original_read_text = type(status_path).read_text

    def deny_status(path, *args, **kwargs):
        if path == status_path:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(status_path), "read_text", deny_status)

    assert read_watchdog_health(status_path) == {
        "state": "unavailable",
        "reason": "status_unreadable",
        "suppression": False,
        "configured": True,
    }


def test_watchdog_health_contains_malformed_counters(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 12, 0, tzinfo=UTC))
    status_path = tmp_path / "status.json"
    atomic_write_json(
        status_path,
        {
            "watchdog_state": "healthy",
            "updated_at_utc": utc_text(clock()),
            "attempt_count": "not-an-integer",
            "restart_count": None,
            "reboot_count": -1,
        },
    )

    result = read_watchdog_health(status_path, max_age_seconds=30, now=clock)

    assert result["state"] == "healthy"
    assert (result["attempt_count"], result["restart_count"], result["reboot_count"]) == (0, 0, 0)


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def config(tmp_path, **overrides) -> WatchdogConfig:
    values = {
        "heartbeat_file": tmp_path / "heartbeat.json",
        "status_file": tmp_path / "status.json",
        "history_file": tmp_path / "history.json",
        "event_dir": tmp_path / "events",
        "startup_grace_seconds": 0,
        "timeout_seconds": 180,
        "cooldown_seconds": 300,
    }
    values.update(overrides)
    return WatchdogConfig(**values)


def heartbeat(
    path,
    now: datetime,
    *,
    boot_id: str = "host-a",
    persisted: datetime | None = None,
    phase: str = "persisted",
    process_id: int = 42,
    instance_id: str | None = None,
    persisted_sample: bool = True,
    disk_status: str | None = None,
) -> None:
    value = {
        "boot_id": boot_id,
        "process_id": process_id,
        "phase": phase,
        "updated_at_utc": utc_text(now),
        "last_persisted_at_utc": utc_text(persisted or now) if persisted_sample else None,
    }
    if disk_status is not None:
        value["disk_status"] = disk_status
    if instance_id is not None:
        value["instance_id"] = instance_id
    atomic_write_json(path, value)


def test_heartbeat_writer_preserves_last_persisted_sample_across_phases(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    writer = HeartbeatWriter(tmp_path / "heartbeat.json", "edge-01", process_id=7, current_boot_id="boot-a", now=clock)

    first = writer.write("collecting:air")
    writer.persisted("record-1")
    clock.advance(5)
    writer.write("collecting:pod_1:soil_temperature")

    value = read_json(tmp_path / "heartbeat.json")
    assert value is not None
    assert value["boot_id"] == "boot-a"
    assert value["process_id"] == 7
    assert value["instance_id"] == first["instance_id"]
    assert value["phase"] == "collecting:pod_1:soil_temperature"
    assert value["last_persisted_record_id"] == "record-1"
    assert value["last_persisted_at_utc"] == "2026-08-17T10:00:00Z"
    assert not list(tmp_path.glob("*.tmp"))


def test_stale_heartbeat_restarts_even_when_phase_identifies_frozen_read(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)
    heartbeat(cfg.heartbeat_file, clock() - timedelta(seconds=181), phase="collecting:pod_1:soil_temperature")
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    assert watchdog.poll() == "restart"
    assert actions == ["restart"]
    assert "soil_temperature" in str(watchdog.state.reason)
    assert watchdog.state.restart_count == 1
    assert len(list(cfg.event_dir.glob("*recovery_started*.json"))) == 1


@pytest.mark.parametrize("persisted_sample", [True, False])
def test_storage_suspension_does_not_consume_recovery_budget(tmp_path, persisted_sample) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)
    actions = []
    heartbeat(
        cfg.heartbeat_file,
        clock(),
        persisted=clock() - timedelta(seconds=181),
        persisted_sample=persisted_sample,
        phase="storage_degraded",
        disk_status="CRITICAL",
    )
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    assert watchdog.poll() == "healthy"
    assert actions == []
    assert watchdog.state.attempt_count == 0
    assert watchdog.state.restart_count == 0
    assert watchdog.state.reason == "storage_degraded"
    assert watchdog.state.result == "storage_suspended"


def test_stale_storage_suspension_heartbeat_still_triggers_recovery(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)
    actions = []
    heartbeat(
        cfg.heartbeat_file,
        clock() - timedelta(seconds=181),
        phase="storage_degraded",
        disk_status="DEGRADED",
    )
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    assert watchdog.poll() == "restart"
    assert actions == ["restart"]
    assert watchdog.state.reason == "heartbeat_stale:storage_degraded"


@pytest.mark.parametrize(
    ("updated_offset", "persisted_offset", "expected_reason"),
    [
        (1, 0, "heartbeat_timestamp_in_future"),
        (0, 1, "persisted_sample_timestamp_in_future"),
    ],
)
def test_future_heartbeat_timestamps_trigger_recovery_and_do_not_grant_grace(
    tmp_path,
    updated_offset,
    persisted_offset,
    expected_reason,
) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)
    heartbeat(
        cfg.heartbeat_file,
        clock() + timedelta(seconds=updated_offset),
        persisted=clock() + timedelta(seconds=persisted_offset),
    )
    actions = []
    watchdog = HostWatchdog(
        cfg,
        action=lambda action: actions.append(action) or True,
        now=clock,
        current_boot_id="host-a",
    )

    assert watchdog.poll() == "restart"
    assert actions == ["restart"]
    assert watchdog.state.reason == expected_reason
    assert watchdog.state.collector_boot_id is None
    assert watchdog.state.collector_process_id is None


def test_storage_suspension_does_not_clear_existing_suppression(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path, startup_grace_seconds=0)
    heartbeat(
        cfg.heartbeat_file,
        clock(),
        persisted_sample=False,
        phase="storage_degraded",
        disk_status="DEGRADED",
    )
    watchdog = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-a")
    watchdog.state.suppression = True
    watchdog.state.watchdog_state = "suppressed"

    assert watchdog.poll() == "healthy"
    assert watchdog.state.suppression is True
    assert watchdog.state.watchdog_state == "suppressed"
    assert watchdog.state.healthy_since_utc is None


def test_storage_suspension_resets_sustained_suppression_recovery_timer(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path, startup_grace_seconds=60)
    heartbeat(cfg.heartbeat_file, clock())
    watchdog = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-a")
    watchdog.state.suppression = True
    watchdog.state.watchdog_state = "suppressed"

    assert watchdog.poll() == "healthy"
    first_healthy_at = watchdog.state.healthy_since_utc
    assert first_healthy_at == utc_text(clock())

    clock.advance(30)
    heartbeat(
        cfg.heartbeat_file,
        clock(),
        persisted_sample=False,
        phase="storage_degraded",
        disk_status="DEGRADED",
    )
    assert watchdog.poll() == "healthy"
    assert watchdog.state.healthy_since_utc is None
    assert watchdog.state.suppression is True

    clock.advance(300)
    heartbeat(cfg.heartbeat_file, clock())
    assert watchdog.poll() == "healthy"
    assert watchdog.state.healthy_since_utc == utc_text(clock())
    assert watchdog.state.suppression is True

    clock.advance(60)
    heartbeat(cfg.heartbeat_file, clock())
    assert watchdog.poll() == "healthy"
    assert watchdog.state.suppression is False
    assert watchdog.state.result == "suppression_cleared"


def test_storage_suspension_does_not_falsely_complete_recovery(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)
    heartbeat(cfg.heartbeat_file, clock() - timedelta(seconds=181))
    watchdog = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-a")
    assert watchdog.poll() == "restart"

    clock.advance(1)
    heartbeat(
        cfg.heartbeat_file,
        clock(),
        persisted=clock() - timedelta(seconds=181),
        phase="storage_degraded",
        disk_status="DEGRADED",
    )

    assert watchdog.poll() == "healthy"
    assert watchdog.state.watchdog_state == "recovering"
    assert watchdog.state.result == "restart_accepted"
    assert not list(cfg.event_dir.glob("*recovery_completed*.json"))

    clock.advance(1)
    heartbeat(cfg.heartbeat_file, clock())
    assert watchdog.poll() == "healthy"
    assert watchdog.state.watchdog_state == "healthy"
    assert watchdog.state.result == "recovered"


def test_heartbeat_from_previous_host_boot_is_rejected(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)
    heartbeat(cfg.heartbeat_file, clock(), boot_id="host-old")
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-new"
    )

    assert watchdog.poll() == "restart"
    assert actions == ["restart"]
    assert watchdog.state.reason == "heartbeat_boot_id_mismatch"
    assert watchdog.state.collector_boot_id is None
    assert watchdog.state.collector_process_id is None


def test_restart_requires_new_persisted_heartbeat_to_complete_recovery(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)
    heartbeat(cfg.heartbeat_file, clock() - timedelta(seconds=181))
    watchdog = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-a")
    watchdog.poll()

    heartbeat(cfg.heartbeat_file, clock(), persisted=clock() - timedelta(seconds=181), phase="collecting:air")
    assert watchdog.poll() == "cooldown"
    assert watchdog.state.watchdog_state == "cooldown"

    clock.advance(1)
    heartbeat(cfg.heartbeat_file, clock())
    assert watchdog.poll() == "healthy"
    assert watchdog.state.result == "recovered"
    assert len(list(cfg.event_dir.glob("*recovery_completed*.json"))) == 1


def test_recovery_survives_restarted_collector_startup_grace(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path, startup_grace_seconds=60)
    heartbeat(cfg.heartbeat_file, clock() - timedelta(seconds=181))
    watchdog = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-a")

    # Expire the watchdog's initial host-start grace before triggering recovery.
    clock.advance(61)
    assert watchdog.poll() == "restart"

    clock.advance(1)
    heartbeat(cfg.heartbeat_file, clock(), phase="startup", process_id=43, persisted_sample=False)
    assert watchdog.poll() == "startup_grace"
    assert watchdog.state.watchdog_state == "recovering"

    watchdog = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-a")
    assert watchdog.state.watchdog_state == "recovering"

    clock.advance(1)
    heartbeat(cfg.heartbeat_file, clock(), process_id=43)
    assert watchdog.poll() == "healthy"
    assert watchdog.state.result == "recovered"
    assert len(list(cfg.event_dir.glob("*recovery_completed*.json"))) == 1


def test_new_collector_process_gets_its_own_startup_grace(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    cfg = config(tmp_path, startup_grace_seconds=60)
    heartbeat(cfg.heartbeat_file, clock())
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )
    assert watchdog.poll() == "healthy"

    clock.advance(120)
    atomic_write_json(
        cfg.heartbeat_file,
        {
            "boot_id": "host-a",
            "process_id": 43,
            "phase": "startup",
            "updated_at_utc": utc_text(clock()),
            "last_persisted_at_utc": None,
        },
    )

    assert watchdog.poll() == "startup_grace"
    assert actions == []

    clock.advance(61)
    assert watchdog.poll() == "restart"
    assert actions == ["restart"]


def test_restarted_collector_with_reused_pid_gets_startup_grace(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    cfg = config(tmp_path, startup_grace_seconds=60)
    heartbeat(cfg.heartbeat_file, clock(), process_id=1, instance_id="collector-a")
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )
    assert watchdog.poll() == "healthy"

    clock.advance(120)
    heartbeat(
        cfg.heartbeat_file,
        clock(),
        phase="startup",
        process_id=1,
        instance_id="collector-b",
        persisted_sample=False,
    )

    assert watchdog.poll() == "startup_grace"
    assert actions == []
    assert watchdog.state.collector_instance_id == "collector-b"


def test_crash_loop_cannot_reset_collector_startup_grace(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    cfg = config(tmp_path, startup_grace_seconds=60, cooldown_seconds=0)
    heartbeat(cfg.heartbeat_file, clock())
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )
    assert watchdog.poll() == "healthy"

    clock.advance(120)
    heartbeat(cfg.heartbeat_file, clock(), phase="startup", process_id=43, persisted_sample=False)
    assert watchdog.poll() == "startup_grace"
    grace_started = watchdog.state.collector_started_at_utc

    clock.advance(30)
    heartbeat(cfg.heartbeat_file, clock(), phase="startup", process_id=44, persisted_sample=False)
    assert watchdog.poll() == "startup_grace"
    assert watchdog.state.collector_started_at_utc == grace_started

    # Reloading the host watchdog must not lose the bounded grace deadline.
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )
    clock.advance(31)
    heartbeat(cfg.heartbeat_file, clock(), phase="startup", process_id=45, persisted_sample=False)

    assert watchdog.poll() == "restart"
    assert actions == ["restart"]
    assert watchdog.state.restart_count == 1


def test_systemd_restart_is_requested_without_blocking_watchdog_notifications(tmp_path, monkeypatch) -> None:
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("src.watchdog.subprocess.run", fake_run)
    watchdog = HostWatchdog(config(tmp_path), current_boot_id="host-a")

    assert watchdog._system_action("restart") is True
    assert commands == [["systemctl", "restart", "--no-block", "senior-pomidor-edge.service"]]


def test_event_persistence_failure_does_not_block_recovery(tmp_path, caplog) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    event_path = tmp_path / "events"
    event_path.write_text("not a directory", encoding="utf-8")
    cfg = config(tmp_path, event_dir=event_path)
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    assert watchdog.poll() == "restart"
    assert actions == ["restart"]
    assert watchdog.state.result == "restart_accepted"
    assert read_json(cfg.status_file)["result"] == "restart_accepted"
    assert read_json(cfg.history_file)["events"][0]["action"] == "restart"
    assert "lifecycle event persistence failed" in caplog.text


def test_recovery_action_exception_is_recorded_as_failure(tmp_path, caplog) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)

    def broken_action(_action: str) -> bool:
        raise RuntimeError("action adapter failed")

    watchdog = HostWatchdog(cfg, action=broken_action, now=clock, current_boot_id="host-a")

    assert watchdog.poll() == "restart"
    assert watchdog.state.result == "restart_failed"
    assert read_json(cfg.status_file)["result"] == "restart_failed"
    assert read_json(cfg.history_file)["events"][0]["result"] == "restart_failed"
    assert "action failed with an exception" in caplog.text


def test_history_persistence_failure_does_not_hide_action_result(tmp_path, caplog) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    history_parent = tmp_path / "history-parent"
    history_parent.write_text("not a directory", encoding="utf-8")
    cfg = config(tmp_path, history_file=history_parent / "history.json")
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    assert watchdog.poll() == "restart"
    assert actions == ["restart"]
    assert read_json(cfg.status_file)["result"] == "restart_accepted"
    assert "history persistence failed" in caplog.text


def test_missing_system_action_command_returns_failure(tmp_path, monkeypatch, caplog) -> None:
    def missing_command(*_args, **_kwargs):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr("src.watchdog.subprocess.run", missing_command)
    watchdog = HostWatchdog(config(tmp_path), current_boot_id="host-a")

    assert watchdog._system_action("restart") is False
    assert "command could not be started" in caplog.text


def test_restart_reboot_budgets_and_persistent_suppression(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    cfg = config(tmp_path, allow_reboot=True)
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    for _ in range(3):
        assert watchdog.poll() == "restart"
        clock.advance(301)
    assert watchdog.poll() == "reboot"
    clock.advance(301)
    assert watchdog.poll() == "suppressed"
    assert actions == ["restart", "restart", "restart", "reboot"]
    assert read_json(cfg.status_file)["suppression"] is True

    reloaded = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-a")
    assert reloaded.poll() == "suppressed"
    assert reloaded.state.restart_count == 3
    assert len(read_json(cfg.history_file)["events"]) == 5


def test_reboot_disabled_suppresses_after_restart_budget(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    cfg = config(tmp_path, allow_reboot=False, restart_limit=1, cooldown_seconds=0)
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    assert watchdog.poll() == "restart"
    assert watchdog.poll() == "suppressed"
    assert actions == ["restart"]


def test_planned_maintenance_suppresses_recovery_without_consuming_budget(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path, cooldown_seconds=0)
    actions = []
    set_maintenance_hold(cfg.maintenance_file, True, reason="sensor service", now=clock())
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    for _ in range(3):
        assert watchdog.poll() == "maintenance"
        clock.advance(301)

    assert actions == []
    assert watchdog.state.watchdog_state == "maintenance"
    assert watchdog.state.result == "recovery_suppressed"
    assert watchdog.state.attempt_count == 0
    assert watchdog.state.restart_count == 0
    assert watchdog.state.reboot_count == 0
    assert read_json(cfg.status_file)["reason"] == "planned_maintenance"

    set_maintenance_hold(cfg.maintenance_file, False)
    assert watchdog.poll() == "restart"
    assert actions == ["restart"]


def test_invalid_maintenance_marker_does_not_disable_recovery(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    cfg = config(tmp_path)
    atomic_write_json(cfg.maintenance_file, {"active": "yes"})
    actions = []
    watchdog = HostWatchdog(
        cfg, action=lambda action: actions.append(action) or True, now=clock, current_boot_id="host-a"
    )

    assert watchdog.poll() == "restart"
    assert actions == ["restart"]


def test_boot_id_change_clears_suppression_but_preserves_hourly_reboot_budget(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    cfg = config(tmp_path)
    atomic_write_json(
        cfg.status_file,
        {
            "watchdog_state": "suppressed",
            "boot_id": "host-old",
            "started_at_utc": utc_text(clock() - timedelta(hours=1)),
            "suppression": True,
            "restart_attempts_utc": [],
            "reboot_attempts_utc": [utc_text(clock() - timedelta(minutes=5))],
            "attempt_count": 4,
            "restart_count": 3,
            "reboot_count": 1,
        },
    )

    watchdog = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-new")

    assert watchdog.state.boot_id == "host-new"
    assert watchdog.state.suppression is False
    assert len(watchdog.state.reboot_attempts_utc) == 1


def test_suppression_clears_only_after_sustained_healthy_heartbeat(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    cfg = config(tmp_path, startup_grace_seconds=60)
    heartbeat(cfg.heartbeat_file, clock())
    watchdog = HostWatchdog(cfg, action=lambda _action: True, now=clock, current_boot_id="host-a")
    watchdog.state.suppression = True
    watchdog.state.watchdog_state = "suppressed"

    assert watchdog.poll() == "healthy"
    assert watchdog.state.suppression is True
    clock.advance(61)
    heartbeat(cfg.heartbeat_file, clock())
    assert watchdog.poll() == "healthy"
    assert watchdog.state.suppression is False
    assert watchdog.state.result == "suppression_cleared"


def test_default_timeout_tracks_three_collection_intervals(tmp_path) -> None:
    cfg = WatchdogConfig.from_env(
        {
            "POLL_INTERVAL_SECONDS": "90",
            "WATCHDOG_HEARTBEAT_FILE": str(tmp_path / "heartbeat.json"),
        }
    )
    assert cfg.timeout_seconds == 270
    assert cfg.allow_reboot is False


def test_blank_optional_timeout_uses_dynamic_default(tmp_path) -> None:
    cfg = WatchdogConfig.from_env(
        {
            "POLL_INTERVAL_SECONDS": "90",
            "WATCHDOG_TIMEOUT_SECONDS": "",
            "WATCHDOG_STARTUP_GRACE_SECONDS": "",
            "WATCHDOG_HEARTBEAT_FILE": str(tmp_path / "heartbeat.json"),
        }
    )
    assert cfg.timeout_seconds == 270
    assert cfg.startup_grace_seconds == 270


@pytest.mark.parametrize("timeout", [59, 60])
def test_host_watchdog_rejects_timeout_not_greater_than_sample_interval(timeout) -> None:
    with pytest.raises(ValueError, match="WATCHDOG_TIMEOUT_SECONDS must be greater than POLL_INTERVAL_SECONDS"):
        WatchdogConfig.from_env(
            {
                "POLL_INTERVAL_SECONDS": "60",
                "WATCHDOG_TIMEOUT_SECONDS": str(timeout),
            }
        )


def test_host_watchdog_accepts_timeout_greater_than_sample_interval() -> None:
    cfg = WatchdogConfig.from_env(
        {
            "POLL_INTERVAL_SECONDS": "60",
            "WATCHDOG_TIMEOUT_SECONDS": "61",
        }
    )

    assert cfg.timeout_seconds == 61


def test_status_and_history_are_valid_atomic_json(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))
    cfg = config(tmp_path)
    watchdog = HostWatchdog(cfg, action=lambda _action: False, now=clock, current_boot_id="host-a")
    watchdog.poll()

    assert json.loads(cfg.status_file.read_text(encoding="utf-8"))["result"] == "restart_failed"
    assert json.loads(cfg.history_file.read_text(encoding="utf-8"))["events"][0]["action"] == "restart"
    assert not list(tmp_path.glob("*.tmp"))


def test_recovery_actions_never_remove_pending_spool_records(tmp_path) -> None:
    spool_path = tmp_path / "telemetry.sqlite3"
    options = {
        "boot_id": "collector-boot",
        "disk_warning_percent": 101,
        "disk_degraded_percent": 102,
        "disk_critical_percent": 103,
    }
    spool = SpoolRepository(spool_path, **options).open()
    record = spool.enqueue(
        {
            "schema_version": "senior-pomidor.edge.telemetry.v2",
            "device_id": "edge-01",
            "timestamp_utc": "2026-08-17T10:00:00Z",
            "pods": {},
            "system_health": {},
        }
    )
    spool.close()

    clock = Clock(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))
    watchdog = HostWatchdog(
        config(tmp_path, allow_reboot=True), action=lambda _action: True, now=clock, current_boot_id="host-a"
    )
    watchdog.poll()

    reopened = SpoolRepository(spool_path, **options).open()
    try:
        assert reopened.get(record.record_id).state == "pending"
        assert reopened.health()["pending_count"] == 1
    finally:
        reopened.close()
