"""Inspect and safely operate the durable telemetry spool."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.telemetry_spool import SpoolError, SpoolRepository  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and maintain the SQLite telemetry spool.")
    parser.add_argument(
        "--db",
        default=os.environ.get("TELEMETRY_SPOOL_DB_PATH", "data/telemetry-spool.sqlite3"),
        help="Path to the spool database.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--state", choices=("pending", "in_flight", "delivered", "dead_letter"))
    list_parser.add_argument("--limit", type=int)
    list_parser.add_argument("--sort", choices=("oldest", "newest"), default="newest")
    show = subparsers.add_parser("show")
    show.add_argument("record_id")
    retry = subparsers.add_parser("retry-dead")
    retry.add_argument("record_id", nargs="?")
    subparsers.add_parser("integrity-check")
    checkpoint = subparsers.add_parser("checkpoint")
    checkpoint.add_argument("--truncate", action="store_true")
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--delivered-retention-days", type=int, default=7)
    backup = subparsers.add_parser("online-backup")
    backup.add_argument("destination")
    export = subparsers.add_parser("export")
    export.add_argument("destination")
    export.add_argument("--state", choices=("pending", "in_flight", "delivered", "dead_letter"))
    export.add_argument("--sort", choices=("oldest", "newest"), default="oldest")
    history = subparsers.add_parser("history")
    history.add_argument("--record-id")
    history.add_argument("--sort", choices=("oldest", "newest"), default="oldest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = SpoolRepository(args.db, recover_in_flight=False).open()
    try:
        if args.command == "status":
            output: object = repository.health()
        elif args.command == "list":
            output = [
                asdict(record) for record in repository.list_records(state=args.state, limit=args.limit, sort=args.sort)
            ]
        elif args.command == "show":
            output = {"record": asdict(repository.get(args.record_id)), "attempts": repository.attempts(args.record_id)}
        elif args.command == "retry-dead":
            output = {"retried": repository.retry_dead(args.record_id)}
        elif args.command == "integrity-check":
            output = {"result": repository.integrity_check()}
        elif args.command == "checkpoint":
            output = {"checkpoint": repository.checkpoint(args.truncate)}
        elif args.command == "cleanup":
            output = {"deleted_delivered": repository.cleanup_delivered(args.delivered_retention_days)}
        elif args.command == "online-backup":
            output = {"backup": str(repository.online_backup(args.destination))}
        elif args.command == "export":
            output = {
                "export": str(args.destination),
                "records": repository.export_records(args.destination, state=args.state, sort=args.sort),
            }
        elif args.command == "history":
            output = (
                repository.attempts(args.record_id, sort=args.sort)
                if args.record_id
                else repository.attempt_history(sort=args.sort)
            )
        else:  # pragma: no cover - argparse enforces commands
            raise SpoolError(f"unknown command {args.command}")
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (KeyError, SpoolError) as exc:
        print(f"telemetry spool error: {exc}", file=sys.stderr)
        return 1
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
