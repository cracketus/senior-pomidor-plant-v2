# Layered edge watchdog

The collector atomically writes `data/watchdog/heartbeat.json` before every potentially blocking sensor read and after a telemetry record is persisted. Each collector process includes a startup UUID so the host can distinguish Docker container incarnations even when the container-local PID is reused. The host supervisor is independent of Docker and treats both a stale progress timestamp and a stale persisted-sample timestamp as failures. A fresh `storage_degraded` heartbeat with a `DEGRADED` or `CRITICAL` disk status is the deliberate exception: it proves the collector is responsive while persistence is intentionally suspended, so it does not consume recovery budgets. A stale progress timestamp still triggers recovery, and suspension cannot complete an existing recovery or clear suppression without a fresh persisted sample. Suspension also resets the sustained-health timer, so suppression clears only after the full configured healthy period following resumed persistence. Lifecycle-event MQTT replay runs in a separate fault-isolated worker so broker outages cannot block sampling or heartbeat progress. `data/watchdog/status.json` and `history.json` are atomic local observability records; they are also summarized in `system_health.watchdog`.

The collector publishes its startup identity before opening or recovering the telemetry spool, ensuring the supervisor recognizes a new process during slow storage initialization. `WATCHDOG_TIMEOUT_SECONDS` must be greater than `POLL_INTERVAL_SECONDS`; invalid combinations fail configuration validation instead of causing a normal sleeping collector to enter a recovery loop.

Recovery is deliberately bounded. The supervisor restarts `senior-pomidor-edge.service` first and only declares recovery after a newer persisted heartbeat appears. It permits three restarts per 30 minutes with a five-minute cooldown. After that, one controlled reboot per hour is possible only when `WATCHDOG_ALLOW_REBOOT=true`. Otherwise it enters persistent suppression. Pending SQLite telemetry and queued lifecycle events are never deleted by recovery; replay starts only after the recovered collector persists its first fresh sample.

Planned maintenance must begin with `python scripts/maintenance_event.py start --reason "..."`. When the host watchdog is installed, the command creates the persistent `WATCHDOG_MAINTENANCE_FILE` marker before publishing the lifecycle event. While that marker is active, the supervisor reports `maintenance`, performs no recovery action, and consumes no restart or reboot budget. Without an installed host watchdog, the hold is unnecessary and the command continues to emit the lifecycle event. After the service is healthy again, run `python scripts/maintenance_event.py complete --reason "..."` to remove the marker and publish completion. A malformed marker does not disable recovery, and a required marker-write failure returns exit code `2` so operators do not mistake an unsafe shutdown for a protected one.

## Installation

Hardware setup installs the host supervisor and Raspberry Pi OS runtime hardware watchdog by default:

```bash
./scripts/setup_raspberry_pi.sh --hardware --install-watchdog
```

The explicit `--install-watchdog` form remains supported. Use `--no-watchdog` to opt a hardware installation out. Mock and ordinary Compose runs do not install a supervisor or perform restart/reboot actions. Controlled reboot remains off unless explicitly enabled in the checkout `.env`.

Both host units and Docker Compose use the checkout `.env`; set `WATCHDOG_SERVICE_NAME=senior-pomidor-edge.service`. The container observes only atomic `data/watchdog/status.json` through the existing `data/` bind mount. Do not mount host DBus, the systemd API, or the Docker socket into it.

Installation creates `data/watchdog/installed` before enabling the services. This marker records supervisor intent independently of `status.json`, so a supervisor that fails before its first poll is reported as configured but unavailable.

Validate the producer/consumer boundary on the host:

```bash
systemctl status senior-pomidor-edge.service senior-pomidor-watchdog.service
sudo cat data/watchdog/status.json
docker compose exec senior-pomidor-edge python -c 'import json; print(json.load(open("data/watchdog/status.json")))'
```

## Disable and manual recovery

```bash
sudo systemctl disable --now senior-pomidor-watchdog.service
sudo python3 scripts/edge_watchdog.py --reset-suppression
sudo systemctl restart senior-pomidor-watchdog.service
```

Before resetting suppression, inspect `data/watchdog/status.json`, `data/watchdog/history.json`, `journalctl -u senior-pomidor-watchdog`, and the phase in `heartbeat.json`. A phase such as `collecting:pod_1:soil_temperature` identifies the read that stopped progressing. To disable the Raspberry Pi hardware watchdog as well, remove `/etc/systemd/system.conf.d/senior-pomidor-hardware-watchdog.conf` and run `sudo systemctl daemon-reexec`.
