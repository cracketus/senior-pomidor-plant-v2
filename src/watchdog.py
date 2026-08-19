"""Collector heartbeat and host-side layered watchdog primitives."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
WATCHDOG_INSTALLATION_MARKER = "installed"


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_text(value: datetime | None = None) -> str:
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def boot_id(path: str | Path = "/proc/sys/kernel/random/boot_id") -> str:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    return f"unknown-{uuid.uuid4()}"


def atomic_write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    temporary.replace(target)


def read_json(path: str | Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class HeartbeatWriter:
    """Atomically expose collector progress to a process outside the container."""

    def __init__(
        self,
        path: str | Path,
        device_id: str,
        *,
        process_id: int | None = None,
        instance_id: str | None = None,
        current_boot_id: str | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = Path(path)
        self.device_id = device_id
        self.process_id = process_id or os.getpid()
        self.instance_id = instance_id or str(uuid.uuid4())
        self.boot_id = current_boot_id or boot_id()
        self.now = now
        self.last_persisted_at: str | None = None
        self.last_record_id: str | None = None

    def write(self, phase: str, **details: Any) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": "senior-pomidor.edge.heartbeat.v1",
            "device_id": self.device_id,
            "boot_id": self.boot_id,
            "process_id": self.process_id,
            "instance_id": self.instance_id,
            "phase": phase,
            "updated_at_utc": utc_text(self.now()),
            "last_persisted_at_utc": self.last_persisted_at,
            "last_persisted_record_id": self.last_record_id,
        }
        value.update(details)
        atomic_write_json(self.path, value)
        return value

    def persisted(self, record_id: str | None = None) -> dict[str, Any]:
        self.last_persisted_at = utc_text(self.now())
        self.last_record_id = record_id
        return self.write("persisted")


@dataclass(frozen=True)
class WatchdogConfig:
    heartbeat_file: Path = Path("data/watchdog/heartbeat.json")
    status_file: Path = Path("data/watchdog/status.json")
    history_file: Path = Path("data/watchdog/history.json")
    maintenance_file: Path = Path("data/watchdog/maintenance.json")
    event_dir: Path = Path("data/events")
    device_id: str = "balcony-edge-01"
    service_name: str = "senior-pomidor-edge.service"
    poll_seconds: int = 15
    timeout_seconds: int = 180
    startup_grace_seconds: int = 180
    restart_limit: int = 3
    restart_window_seconds: int = 1800
    cooldown_seconds: int = 300
    reboot_limit: int = 1
    reboot_window_seconds: int = 3600
    allow_reboot: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WatchdogConfig:
        env = env or os.environ
        poll = _env_int(env, "WATCHDOG_POLL_SECONDS", 15, 1)
        interval = _env_int(env, "POLL_INTERVAL_SECONDS", 60, 1)
        timeout = _env_int(env, "WATCHDOG_TIMEOUT_SECONDS", max(180, interval * 3), 1)
        if timeout <= interval:
            raise ValueError("WATCHDOG_TIMEOUT_SECONDS must be greater than POLL_INTERVAL_SECONDS")
        return cls(
            heartbeat_file=Path(env.get("WATCHDOG_HEARTBEAT_FILE", "data/watchdog/heartbeat.json")),
            status_file=Path(env.get("WATCHDOG_STATUS_FILE", "data/watchdog/status.json")),
            history_file=Path(env.get("WATCHDOG_HISTORY_FILE", "data/watchdog/history.json")),
            maintenance_file=Path(env.get("WATCHDOG_MAINTENANCE_FILE", "data/watchdog/maintenance.json")),
            event_dir=Path(env.get("LOCAL_EVENT_DIR", "data/events")),
            device_id=env.get("DEVICE_ID", "balcony-edge-01"),
            service_name=env.get("WATCHDOG_SERVICE_NAME", "senior-pomidor-edge.service"),
            poll_seconds=poll,
            timeout_seconds=timeout,
            startup_grace_seconds=_env_int(env, "WATCHDOG_STARTUP_GRACE_SECONDS", max(180, interval * 3), 0),
            restart_limit=_env_int(env, "WATCHDOG_RESTART_LIMIT", 3, 1),
            restart_window_seconds=_env_int(env, "WATCHDOG_RESTART_WINDOW_SECONDS", 1800, 1),
            cooldown_seconds=_env_int(env, "WATCHDOG_COOLDOWN_SECONDS", 300, 0),
            reboot_limit=_env_int(env, "WATCHDOG_REBOOT_LIMIT", 1, 0),
            reboot_window_seconds=_env_int(env, "WATCHDOG_REBOOT_WINDOW_SECONDS", 3600, 1),
            allow_reboot=_env_bool(env, "WATCHDOG_ALLOW_REBOOT", False),
        )


@dataclass
class WatchdogState:
    watchdog_state: str = "starting"
    reason: str | None = None
    result: str | None = None
    boot_id: str = field(default_factory=boot_id)
    started_at_utc: str = field(default_factory=utc_text)
    last_healthy_heartbeat_at_utc: str | None = None
    recovery_started_at_utc: str | None = None
    healthy_since_utc: str | None = None
    collector_boot_id: str | None = None
    collector_process_id: int | None = None
    collector_instance_id: str | None = None
    collector_started_at_utc: str | None = None
    collector_became_healthy: bool = False
    suppression: bool = False
    restart_attempts_utc: list[str] = field(default_factory=list)
    reboot_attempts_utc: list[str] = field(default_factory=list)
    attempt_count: int = 0
    restart_count: int = 0
    reboot_count: int = 0

    @classmethod
    def load(cls, path: str | Path, current_boot_id: str) -> WatchdogState:
        raw = read_json(path)
        if not raw:
            return cls(boot_id=current_boot_id)
        allowed = set(cls.__dataclass_fields__)
        state = cls(**{key: value for key, value in raw.items() if key in allowed})
        if state.boot_id != current_boot_id:
            state.boot_id = current_boot_id
            state.started_at_utc = utc_text()
            state.watchdog_state = "starting"
            state.reason = "host_boot_changed"
            state.result = None
            state.recovery_started_at_utc = None
            state.healthy_since_utc = None
            state.collector_boot_id = None
            state.collector_process_id = None
            state.collector_instance_id = None
            state.collector_started_at_utc = None
            state.collector_became_healthy = False
            state.suppression = False
        return state

    def as_dict(self, now: datetime) -> dict[str, Any]:
        return {**self.__dict__, "updated_at_utc": utc_text(now)}


class HostWatchdog:
    """Bounded service restart/reboot state machine driven by the shared heartbeat."""

    def __init__(
        self,
        config: WatchdogConfig,
        *,
        action: Callable[[str], bool] | None = None,
        now: Callable[[], datetime] = utc_now,
        current_boot_id: str | None = None,
    ) -> None:
        self.config = config
        self.now = now
        self.boot_id = current_boot_id or boot_id()
        previous = read_json(config.status_file)
        self.state = WatchdogState.load(config.status_file, self.boot_id)
        if previous is None or previous.get("boot_id") != self.boot_id:
            self.state.started_at_utc = utc_text(self.now())
        self.action = action or self._system_action

    def poll(self) -> str:
        now = self.now()
        self._prune_attempts(now)
        if maintenance_hold_active(self.config.maintenance_file):
            self.state.healthy_since_utc = None
            self.state.watchdog_state = "maintenance"
            self.state.reason = "planned_maintenance"
            self.state.result = "recovery_suppressed"
            self._save(now)
            return "maintenance"
        heartbeat = read_json(self.config.heartbeat_file)
        self._observe_collector(heartbeat, now)
        healthy, reason = self._healthy(heartbeat, now)

        if healthy:
            assert heartbeat is not None
            persisted_value = heartbeat.get("last_persisted_at_utc")
            observed = persisted_value if isinstance(persisted_value, str) else None
            if parse_utc(observed) is not None:
                self.state.last_healthy_heartbeat_at_utc = observed
            self.state.collector_started_at_utc = None
            self.state.collector_became_healthy = True
            self._handle_healthy(now, observed, reason)
            self._save(now)
            return "healthy"

        self.state.healthy_since_utc = None
        self.state.reason = reason
        if self._in_startup_grace(now):
            if self.state.watchdog_state != "recovering":
                self.state.watchdog_state = "starting"
            self.state.result = "startup_grace"
            self._save(now)
            return "startup_grace"
        if self.state.suppression:
            self.state.watchdog_state = "suppressed"
            self.state.result = "budget_exhausted"
            self._save(now)
            return "suppressed"
        if not self._cooldown_elapsed(now):
            self.state.watchdog_state = "cooldown"
            self.state.result = "waiting"
            self._save(now)
            return "cooldown"

        if len(self.state.restart_attempts_utc) < self.config.restart_limit:
            return self._recover("restart", now)
        if self.config.allow_reboot and len(self.state.reboot_attempts_utc) < self.config.reboot_limit:
            return self._recover("reboot", now)

        self.state.suppression = True
        self.state.watchdog_state = "suppressed"
        self.state.result = "budget_exhausted"
        self._record("suppressed", now, reason=reason, result="budget_exhausted")
        self._save(now)
        return "suppressed"

    def clear_suppression(self, reason: str = "manual_reset") -> None:
        now = self.now()
        self.state.suppression = False
        self.state.watchdog_state = "starting"
        self.state.reason = reason
        self.state.result = "suppression_cleared"
        self.state.restart_attempts_utc.clear()
        self.state.reboot_attempts_utc.clear()
        self._record("suppression_cleared", now, reason=reason, result="suppression_cleared")
        self._save(now)

    def _healthy(self, heartbeat: dict[str, Any] | None, now: datetime) -> tuple[bool, str]:
        if heartbeat is None:
            return False, "heartbeat_missing_or_invalid"
        if heartbeat.get("boot_id") != self.boot_id:
            return False, "heartbeat_boot_id_mismatch"
        updated = parse_utc(heartbeat.get("updated_at_utc"))
        persisted = parse_utc(heartbeat.get("last_persisted_at_utc"))
        if updated is None:
            return False, "heartbeat_timestamp_invalid"
        if updated > now:
            return False, "heartbeat_timestamp_in_future"
        if now - updated > timedelta(seconds=self.config.timeout_seconds):
            return False, f"heartbeat_stale:{heartbeat.get('phase', 'unknown')}"
        if persisted is not None and persisted > now:
            return False, "persisted_sample_timestamp_in_future"
        if heartbeat.get("phase") == "storage_degraded" and heartbeat.get("disk_status") in {
            "DEGRADED",
            "CRITICAL",
        }:
            return True, "storage_degraded"
        if persisted is None or now - persisted > timedelta(seconds=self.config.timeout_seconds):
            return False, f"persisted_sample_stale:{heartbeat.get('phase', 'unknown')}"
        return True, "healthy"

    def _handle_healthy(self, now: datetime, observed: str | None, health_reason: str) -> None:
        if health_reason == "storage_degraded":
            # The collector is responsive but has intentionally suspended persistence.
            # Do not spend recovery budgets, complete recovery, or clear suppression
            # until an actual fresh persisted sample arrives.
            self.state.healthy_since_utc = None
            if self.state.suppression:
                self.state.watchdog_state = "suppressed"
            elif self.state.watchdog_state not in {"recovering", "cooldown"}:
                self.state.watchdog_state = "healthy"
                self.state.result = "storage_suspended"
            self.state.reason = "storage_degraded"
            return
        if self.state.watchdog_state in {"recovering", "cooldown"}:
            started = parse_utc(self.state.recovery_started_at_utc)
            persisted = parse_utc(observed)
            if started is not None and persisted is not None and persisted > started:
                self.state.watchdog_state = "healthy"
                self.state.reason = "fresh_persisted_heartbeat"
                self.state.result = "recovered"
                self.state.recovery_started_at_utc = None
                self._queue_event("recovery_completed", "fresh persisted heartbeat", now)
                self._record("recovery_completed", now, result="recovered")
        elif self.state.suppression:
            if self.state.healthy_since_utc is None:
                self.state.healthy_since_utc = utc_text(now)
            since = parse_utc(self.state.healthy_since_utc)
            if since is not None and now - since >= timedelta(seconds=self.config.startup_grace_seconds):
                self.clear_suppression("sustained_recovery")
                self.state.watchdog_state = "healthy"
                self.state.reason = "sustained_recovery"
                self.state.result = "suppression_cleared"
                self._queue_event("recovery_completed", "sustained recovery cleared suppression", now)
        else:
            self.state.watchdog_state = "healthy"
            self.state.reason = None
            self.state.result = "healthy"

    def _recover(self, kind: str, now: datetime) -> str:
        self.state.attempt_count += 1
        attempts = self.state.restart_attempts_utc if kind == "restart" else self.state.reboot_attempts_utc
        attempts.append(utc_text(now))
        if kind == "restart":
            self.state.restart_count += 1
        else:
            self.state.reboot_count += 1
        self.state.watchdog_state = "recovering"
        self.state.recovery_started_at_utc = utc_text(now)
        self.state.result = f"{kind}_requested"
        self._queue_event("recovery_started", f"watchdog {kind}: {self.state.reason}", now)
        # Persist the consumed budget before invoking reboot; the host may stop before the call returns.
        self._save(now)
        try:
            succeeded = bool(self.action(kind))
        except Exception as exc:
            logger.exception("Watchdog %s action failed with an exception: %s", kind, exc)
            succeeded = False
        self.state.result = f"{kind}_{'accepted' if succeeded else 'failed'}"
        self._record(kind, now, reason=self.state.reason, result=self.state.result)
        self._save(now)
        return kind

    def _in_startup_grace(self, now: datetime) -> bool:
        grace = timedelta(seconds=self.config.startup_grace_seconds)
        host_started = parse_utc(self.state.started_at_utc)
        collector_started = parse_utc(self.state.collector_started_at_utc)
        return (host_started is not None and now - host_started < grace) or (
            collector_started is not None and now - collector_started < grace
        )

    def _observe_collector(self, heartbeat: dict[str, Any] | None, now: datetime) -> None:
        if heartbeat is None:
            return
        updated = parse_utc(heartbeat.get("updated_at_utc"))
        persisted = parse_utc(heartbeat.get("last_persisted_at_utc"))
        collector_boot_id = heartbeat.get("boot_id")
        collector_process_id = heartbeat.get("process_id")
        collector_instance_id = heartbeat.get("instance_id")
        if (
            updated is None
            or updated > now
            or (persisted is not None and persisted > now)
            or now - updated > timedelta(seconds=self.config.timeout_seconds)
            or not isinstance(collector_boot_id, str)
            or collector_boot_id != self.boot_id
            or not isinstance(collector_process_id, int)
            or (collector_instance_id is not None and not isinstance(collector_instance_id, str))
        ):
            return
        identity = (collector_boot_id, collector_process_id, collector_instance_id)
        previous = (
            self.state.collector_boot_id,
            self.state.collector_process_id,
            self.state.collector_instance_id,
        )
        if identity != previous:
            # A process that crashes before persisting a healthy sample must not
            # extend startup grace by returning with a new PID. Keep the current
            # deadline across that crash loop; a healthy predecessor earns the
            # next collector process a fresh grace window.
            grant_fresh_grace = previous == (None, None, None) or self.state.collector_became_healthy
            self.state.collector_boot_id = collector_boot_id
            self.state.collector_process_id = collector_process_id
            self.state.collector_instance_id = collector_instance_id
            self.state.collector_became_healthy = False
            if grant_fresh_grace:
                self.state.collector_started_at_utc = utc_text(now)

    def _cooldown_elapsed(self, now: datetime) -> bool:
        all_attempts = self.state.restart_attempts_utc + self.state.reboot_attempts_utc
        parsed_attempts = [parsed for value in all_attempts if (parsed := parse_utc(value)) is not None]
        latest = max(parsed_attempts, default=None)
        return latest is None or now - latest >= timedelta(seconds=self.config.cooldown_seconds)

    def _prune_attempts(self, now: datetime) -> None:
        self.state.restart_attempts_utc = _recent(
            self.state.restart_attempts_utc, now, self.config.restart_window_seconds
        )
        self.state.reboot_attempts_utc = _recent(self.state.reboot_attempts_utc, now, self.config.reboot_window_seconds)

    def _system_action(self, kind: str) -> bool:
        command = (
            ["systemctl", "restart", "--no-block", self.config.service_name]
            if kind == "restart"
            else ["systemctl", "reboot"]
        )
        try:
            return subprocess.run(command, check=False).returncode == 0
        except OSError as exc:
            logger.error("Watchdog %s command could not be started: %s", kind, exc)
            return False

    def _save(self, now: datetime) -> None:
        atomic_write_json(self.config.status_file, self.state.as_dict(now))

    def _record(self, action: str, now: datetime, **details: Any) -> None:
        entry = {
            "timestamp_utc": utc_text(now),
            "action": action,
            "boot_id": self.boot_id,
            "watchdog_state": self.state.watchdog_state,
            "attempt_count": self.state.attempt_count,
            "restart_count": self.state.restart_count,
            "reboot_count": self.state.reboot_count,
            "suppression": self.state.suppression,
            **details,
        }
        try:
            existing = read_json(self.config.history_file)
            entries = existing.get("events", []) if existing else []
            if not isinstance(entries, list):
                entries = []
            atomic_write_json(self.config.history_file, {"events": [*entries[-199:], entry]})
        except Exception as exc:  # noqa: BLE001 - history is diagnostic and must not block state transitions
            logger.error("Watchdog history persistence failed for %s: %s", action, exc)

    def _queue_event(self, event_type: str, reason: str, now: datetime) -> None:
        try:
            event = {
                "schema_version": "senior-pomidor.edge.event.v1",
                "event_id": str(uuid.uuid4()),
                "device_id": self.config.device_id,
                "event_type": event_type,
                "timestamp_utc": utc_text(now),
                "source": "host_watchdog",
                "reason": reason,
            }
            name = f"{utc_text(now).replace(':', '-')}_{event_type}_{event['event_id']}.json"
            atomic_write_json(self.config.event_dir / name, event)
        except Exception as exc:  # noqa: BLE001 - lifecycle events are best-effort recovery diagnostics
            logger.error("Watchdog lifecycle event persistence failed for %s: %s", event_type, exc)


def read_watchdog_health(
    path: str | Path,
    *,
    max_age_seconds: float = 30.0,
    now: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    status = read_json(path)
    configured = watchdog_is_configured(path)
    if status is None:
        return {"state": "unavailable", "suppression": False, "configured": configured}

    updated = parse_utc(status.get("updated_at_utc"))
    current = now()
    current = current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)
    try:
        maximum_age = timedelta(seconds=max_age_seconds)
    except (OverflowError, TypeError, ValueError):
        maximum_age = timedelta(seconds=30)
    if maximum_age <= timedelta(0):
        maximum_age = timedelta(seconds=30)
    if updated is None or updated > current or current - updated > maximum_age:
        reason = "status_timestamp_invalid" if updated is None or updated > current else "status_stale"
        return {
            "state": "unavailable",
            "reason": reason,
            "suppression": False,
            "configured": True,
        }

    return {
        "state": status.get("watchdog_state", "unknown"),
        "reason": status.get("reason"),
        "result": status.get("result"),
        "suppression": status.get("suppression") is True,
        "configured": True,
        "attempt_count": _safe_nonnegative_int(status.get("attempt_count")),
        "restart_count": _safe_nonnegative_int(status.get("restart_count")),
        "reboot_count": _safe_nonnegative_int(status.get("reboot_count")),
        "last_healthy_heartbeat_at_utc": status.get("last_healthy_heartbeat_at_utc"),
        "boot_id": status.get("boot_id"),
    }


def _safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, str | bytes | bytearray | int | float):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, parsed)


def watchdog_is_configured(status_path: str | Path) -> bool:
    """Return installation intent without trusting a successful watchdog poll."""
    status = Path(status_path)
    marker = status.with_name(WATCHDOG_INSTALLATION_MARKER)
    return status.is_file() or marker.is_file()


def set_maintenance_hold(
    path: str | Path,
    active: bool,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> None:
    target = Path(path)
    if not active:
        target.unlink(missing_ok=True)
        return
    value: dict[str, Any] = {
        "active": True,
        "started_at_utc": utc_text(now),
    }
    if reason:
        value["reason"] = reason
    atomic_write_json(target, value)


def maintenance_hold_active(path: str | Path) -> bool:
    value = read_json(path)
    return value is not None and value.get("active") is True


def notify_systemd(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    unix_family = getattr(socket, "AF_UNIX", None)
    if unix_family is None:
        return
    if address.startswith("@"):  # abstract namespace socket
        address = "\0" + address[1:]
    with socket.socket(unix_family, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        sock.sendall(message.encode())


def _recent(values: list[str], now: datetime, window_seconds: int) -> list[str]:
    cutoff = now - timedelta(seconds=window_seconds)
    return [value for value in values if (parsed := parse_utc(value)) is not None and parsed >= cutoff]


def _env_int(env: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    raw = env.get(key)
    try:
        value = int(raw) if raw is not None and raw.strip() else default
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{key} must be >= {minimum}")
    return value


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean")
