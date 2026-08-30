"""Exercise the production host watchdog, then deliberately stop status refreshes."""

from __future__ import annotations

import os
import time

from src.watchdog import HostWatchdog, WatchdogConfig, read_json


def main() -> None:
    config = WatchdogConfig.from_env()
    config.status_file.parent.mkdir(parents=True, exist_ok=True)
    config.status_file.with_name("installed").touch()

    deadline = time.monotonic() + float(os.environ.get("HEARTBEAT_WAIT_SECONDS", "20"))
    while time.monotonic() < deadline:
        heartbeat = read_json(config.heartbeat_file)
        if heartbeat and heartbeat.get("last_persisted_at_utc"):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("collector did not publish a persisted heartbeat")

    collector_boot_id = heartbeat.get("boot_id")
    if not isinstance(collector_boot_id, str) or not collector_boot_id:
        raise RuntimeError("collector heartbeat did not contain a boot id")
    watchdog = HostWatchdog(config, action=lambda _action: False, current_boot_id=collector_boot_id)
    produce_until = time.monotonic() + float(os.environ.get("STATUS_PRODUCE_SECONDS", "6"))
    while time.monotonic() < produce_until:
        watchdog.poll()
        time.sleep(config.poll_seconds)

    print("host-watchdog-fixture: stopped status refreshes", flush=True)
    while True:
        time.sleep(30)


if __name__ == "__main__":
    main()
