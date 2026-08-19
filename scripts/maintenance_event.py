"""Emit planned maintenance lifecycle events from the edge node."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import ConfigError, load_config  # noqa: E402
from src.lifecycle import emit_lifecycle_event  # noqa: E402
from src.utils.events import MAINTENANCE_COMPLETED, MAINTENANCE_STARTED  # noqa: E402
from src.utils.logger import configure_logger  # noqa: E402
from src.watchdog import set_maintenance_hold, watchdog_is_configured  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit a planned maintenance lifecycle event.")
    parser.add_argument("action", choices=("start", "complete"))
    parser.add_argument("--reason", default=None, help="Short operator note stored with the lifecycle event.")
    args = parser.parse_args(argv)

    event_type = MAINTENANCE_STARTED if args.action == "start" else MAINTENANCE_COMPLETED
    logger = configure_logger()
    try:
        settings = load_config()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    if watchdog_is_configured(settings.watchdog_status_file):
        try:
            set_maintenance_hold(
                settings.watchdog_maintenance_file,
                args.action == "start",
                reason=args.reason,
            )
        except OSError as exc:
            logger.error("Maintenance hold update failed; refusing lifecycle transition: %s", exc)
            return 2

    try:
        delivered = emit_lifecycle_event(settings, event_type, reason=args.reason, logger=logger)
    except Exception as exc:  # noqa: BLE001 - operator command must report failures without a traceback
        logger.error("Lifecycle event failed unexpectedly: %s", exc)
        return 2
    if delivered:
        logger.info("Lifecycle event delivered: %s", event_type)
        return 0

    logger.error("Lifecycle event queued for retry: %s", event_type)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
