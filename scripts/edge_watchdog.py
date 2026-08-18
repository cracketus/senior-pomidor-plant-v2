#!/usr/bin/env python3
"""Run the independent Senior Pomidor host watchdog."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.watchdog import HostWatchdog, WatchdogConfig, notify_systemd  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Evaluate once without host daemon loop")
    parser.add_argument("--reset-suppression", action="store_true", help="Clear persistent suppression and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = WatchdogConfig.from_env()
    except ValueError as exc:
        logging.error("Invalid watchdog configuration: %s", exc)
        return 2
    watchdog = HostWatchdog(config)
    if args.reset_suppression:
        watchdog.clear_suppression()
        return 0
    notify_systemd("READY=1")
    while True:
        result = watchdog.poll()
        notify_systemd(f"WATCHDOG=1\nSTATUS={result}")
        if args.once:
            return 0
        remaining = config.poll_seconds
        while remaining > 0:
            interval = min(10, remaining)
            time.sleep(interval)
            remaining -= interval
            notify_systemd("WATCHDOG=1")


if __name__ == "__main__":
    raise SystemExit(main())
