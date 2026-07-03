"""Validate Raspberry Pi host readiness before running the edge service."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import ConfigError, Settings, load_config  # noqa: E402

CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
Connector = Callable[[tuple[str, int], float], Any]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str
    remediation: str = ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Raspberry Pi hardware and server readiness.")
    parser.add_argument("--env-file", default=".env", help="Environment file to read before checking readiness.")
    parser.add_argument("--timeout", type=positive_float, default=3.0, help="Network command timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    env.update(read_env_file(Path(args.env_file)))
    try:
        settings = load_config(env, platform_name="Linux")
    except ConfigError as exc:
        result = CheckResult("configuration", "fail", str(exc), "Fix .env before running the edge service.")
        print_results([result], json_output=args.json)
        return 1

    results = run_checks(settings, timeout_seconds=args.timeout)
    print_results(results, json_output=args.json)
    return 1 if any(result.status == "fail" for result in results) else 0


def run_checks(
    settings: Settings,
    *,
    timeout_seconds: float = 3.0,
    root: Path = Path("/"),
    runner: CommandRunner | None = None,
    connector: Connector | None = None,
) -> list[CheckResult]:
    runner = runner or run_command
    connector = connector or socket.create_connection
    return [
        check_camera(settings, root=root, runner=runner, timeout_seconds=timeout_seconds),
        check_i2c(settings, root=root),
        check_one_wire(settings, root=root),
        check_tcp_endpoint(
            "mqtt",
            settings.mqtt_host,
            settings.mqtt_port,
            connector=connector,
            timeout_seconds=timeout_seconds,
            remediation="Verify MQTT_HOST/MQTT_PORT, broker status, firewall, and LAN routing.",
        ),
        check_http_api(settings, connector=connector, timeout_seconds=timeout_seconds),
        check_time_sync(runner=runner, timeout_seconds=timeout_seconds),
    ]


def check_camera(
    settings: Settings,
    *,
    root: Path,
    runner: CommandRunner,
    timeout_seconds: float,
) -> CheckResult:
    if not settings.camera_enabled:
        return CheckResult("camera", "ok", "CAMERA_ENABLED=false; camera check skipped.")

    device_path = root / settings.camera_device.lstrip("/")
    if not device_path.exists():
        return CheckResult(
            "camera",
            "fail",
            f"Camera device is missing: {settings.camera_device}",
            "Connect the USB camera, verify /dev/video*, or update CAMERA_DEVICE.",
        )
    if command_exists("v4l2-ctl", runner=runner, timeout_seconds=timeout_seconds):
        output = runner(["v4l2-ctl", "--device", settings.camera_device, "--all"], timeout_seconds)
        if output.returncode != 0:
            return CheckResult(
                "camera",
                "fail",
                f"v4l2-ctl cannot read {settings.camera_device}: {stderr_or_stdout(output)}",
                "Install v4l-utils and verify the camera on the Raspberry Pi host.",
            )
    return CheckResult("camera", "ok", f"Camera device is available: {settings.camera_device}")


def check_i2c(settings: Settings, *, root: Path) -> CheckResult:
    if settings.mock_sensors:
        return CheckResult("i2c", "ok", "MOCK_SENSORS=true; I2C check skipped.")
    if not (root / "dev/i2c-1").exists():
        return CheckResult(
            "i2c",
            "fail",
            "/dev/i2c-1 is missing.",
            "Enable I2C with raspi-config or dtparam=i2c_arm=on, then reboot.",
        )
    return CheckResult("i2c", "ok", "/dev/i2c-1 is available.")


def check_one_wire(settings: Settings, *, root: Path) -> CheckResult:
    required_roms = [rom for rom in (settings.ds18b20_pod1_rom, settings.ds18b20_pod2_rom) if rom]
    if settings.mock_sensors:
        return CheckResult("one_wire", "ok", "MOCK_SENSORS=true; 1-Wire check skipped.")
    if not required_roms:
        return CheckResult(
            "one_wire",
            "fail",
            "No DS18B20 ROM IDs are configured.",
            "Set DS18B20_POD1_ROM/DS18B20_POD2_ROM or disable pods without soil temperature sensors.",
        )
    devices_dir = root / "sys/bus/w1/devices"
    missing = [rom for rom in required_roms if not (devices_dir / rom).exists()]
    if missing:
        return CheckResult(
            "one_wire",
            "fail",
            f"Configured DS18B20 ROM IDs are not visible: {', '.join(missing)}",
            "Enable 1-Wire with dtoverlay=w1-gpio, check wiring and pull-up resistor, then reboot.",
        )
    return CheckResult("one_wire", "ok", "Configured DS18B20 devices are visible.")


def check_tcp_endpoint(
    name: str,
    host: str,
    port: int,
    *,
    connector: Connector,
    timeout_seconds: float,
    remediation: str,
) -> CheckResult:
    try:
        connection = connector((host, port), timeout_seconds)
        close = getattr(connection, "close", None)
        if callable(close):
            close()
    except OSError as exc:
        return CheckResult(name, "fail", f"Cannot connect to {host}:{port}: {exc}", remediation)
    return CheckResult(name, "ok", f"Connected to {host}:{port}.")


def check_http_api(settings: Settings, *, connector: Connector, timeout_seconds: float) -> CheckResult:
    if not settings.core_http_url:
        return CheckResult(
            "server_api",
            "fail",
            "CORE_HTTP_URL is not configured.",
            "Set CORE_HTTP_URL to the Senior Pomidor server API, for example http://core:8000/api/v1/edge/telemetry.",
        )
    parsed = urlparse(settings.core_http_url)
    if not parsed.hostname:
        return CheckResult("server_api", "fail", f"CORE_HTTP_URL is invalid: {settings.core_http_url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return check_tcp_endpoint(
        "server_api",
        parsed.hostname,
        port,
        connector=connector,
        timeout_seconds=timeout_seconds,
        remediation="Verify CORE_HTTP_URL, server status, firewall, and LAN routing.",
    )


def check_time_sync(*, runner: CommandRunner, timeout_seconds: float) -> CheckResult:
    output = runner(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout_seconds)
    if output.returncode != 0:
        return CheckResult(
            "time_sync",
            "warn",
            f"timedatectl is unavailable: {stderr_or_stdout(output)}",
            "Install/enable systemd-timesyncd or another NTP client and verify system time manually.",
        )
    if output.stdout.strip().lower() == "yes":
        return CheckResult("time_sync", "ok", "System time is synchronized.")
    return CheckResult(
        "time_sync",
        "fail",
        "System time is not synchronized.",
        "Enable NTP with timedatectl set-ntp true and check network/DNS access.",
    )


def command_exists(command: str, *, runner: CommandRunner, timeout_seconds: float) -> bool:
    return runner(["sh", "-c", f"command -v {command}"], timeout_seconds).returncode == 0


def run_command(command: Sequence[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(list(command), 127, "", str(exc))


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def print_results(results: list[CheckResult], *, json_output: bool) -> None:
    if json_output:
        import json

        print(json.dumps({"checks": [result.__dict__ for result in results]}, indent=2, sort_keys=True))
        return
    for result in results:
        line = f"[{result.status}] {result.name}: {result.message}"
        if result.remediation:
            line = f"{line} Fix: {result.remediation}"
        print(line)


def stderr_or_stdout(output: subprocess.CompletedProcess[str]) -> str:
    return (output.stderr or output.stdout or f"exit {output.returncode}").strip()


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
