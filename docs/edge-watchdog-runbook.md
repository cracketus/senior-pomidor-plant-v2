# Layered edge watchdog

The collector atomically writes `data/watchdog/heartbeat.json` before every potentially blocking sensor read and after a telemetry record is persisted. Each collector process includes a startup UUID so the host can distinguish Docker container incarnations even when the container-local PID is reused. The host supervisor is independent of Docker and treats both a stale progress timestamp and a stale persisted-sample timestamp as failures. A fresh `storage_degraded` heartbeat with a `DEGRADED` or `CRITICAL` disk status is the deliberate exception: it proves the collector is responsive while persistence is intentionally suspended, so it does not consume recovery budgets. A stale progress timestamp still triggers recovery, and suspension cannot complete an existing recovery or clear suppression without a fresh persisted sample. Suspension also resets the sustained-health timer, so suppression clears only after the full configured healthy period following resumed persistence. Lifecycle-event MQTT replay runs in a separate fault-isolated worker so broker outages cannot block sampling or heartbeat progress. `data/watchdog/status.json` and `history.json` are atomic local observability records; they are also summarized in `system_health.watchdog`.

The collector publishes its startup identity before opening or recovering the telemetry spool, ensuring the supervisor recognizes a new process during slow storage initialization. `WATCHDOG_TIMEOUT_SECONDS` must be greater than `POLL_INTERVAL_SECONDS`; invalid combinations fail configuration validation instead of causing a normal sleeping collector to enter a recovery loop.

Recovery is deliberately bounded. The supervisor restarts `senior-pomidor-edge.service` first and only declares recovery after a newer persisted heartbeat appears. It permits three restarts per 30 minutes with a five-minute cooldown. After that, one controlled reboot per hour is possible only when `WATCHDOG_ALLOW_REBOOT=true`. Otherwise it enters persistent suppression. Pending SQLite telemetry and queued lifecycle events are never deleted by recovery; replay starts only after the recovered collector persists its first fresh sample.

Planned maintenance must begin with `python scripts/maintenance_event.py start --reason "..."`. The command creates the persistent `WATCHDOG_MAINTENANCE_FILE` marker before publishing the lifecycle event. While that marker is active, the supervisor reports `maintenance`, performs no recovery action, and consumes no restart or reboot budget. After the service is healthy again, run `python scripts/maintenance_event.py complete --reason "..."` to remove the marker and publish completion. A malformed marker does not disable recovery, and a marker-write failure returns exit code `2` so operators do not mistake an unsafe shutdown for a protected one.

## Installation

Host actions and Raspberry Pi OS runtime hardware watchdog support are explicit opt-ins:

```bash
./scripts/setup_raspberry_pi.sh --hardware --install-watchdog
```

Normal setup and mock Compose runs only produce heartbeat data; they never install a host supervisor or perform restart/reboot actions. Controlled reboot remains off unless explicitly enabled in `.env`.

## Disable and manual recovery

```bash
sudo systemctl disable --now senior-pomidor-watchdog.service
sudo python3 scripts/edge_watchdog.py --reset-suppression
sudo systemctl restart senior-pomidor-watchdog.service
```

Before resetting suppression, inspect `data/watchdog/status.json`, `data/watchdog/history.json`, `journalctl -u senior-pomidor-watchdog`, and the phase in `heartbeat.json`. A phase such as `collecting:pod_1:soil_temperature` identifies the read that stopped progressing. To disable the Raspberry Pi hardware watchdog as well, remove `/etc/systemd/system.conf.d/senior-pomidor-hardware-watchdog.conf` and run `sudo systemctl daemon-reexec`.
