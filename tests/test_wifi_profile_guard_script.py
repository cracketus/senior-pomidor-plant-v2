import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "wifi_profile_guard.sh"


pytestmark = pytest.mark.skipif(os.name == "nt", reason="Bash guard integration uses POSIX paths")


def test_wifi_profile_guard_writes_ok_status_for_healthy_network(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    profile_dir = tmp_path / "profiles"
    backup_dir = tmp_path / "backups"
    status_file = tmp_path / "status.json"
    profile_dir.mkdir()
    (profile_dir / "greenhouse.nmconnection").write_text("[connection]\n", encoding="utf-8")
    _write_fake_command(fake_bin, "nmcli", "printf '%s\\n' 'wlan0:connected'\n")
    _write_fake_command(fake_bin, "ip", "printf '%s\\n' 'default via 192.0.2.1 dev wlan0'\n")
    _write_fake_command(fake_bin, "ping", "exit 0\n")
    _write_fake_command(fake_bin, "getent", "printf '%s\\n' '1.1.1.1 example.com'\n")

    result = _run_guard(tmp_path, fake_bin, profile_dir, backup_dir, status_file)

    assert result.returncode == 0
    assert json.loads(status_file.read_text(encoding="utf-8"))["result"] == "ok"
    assert list(backup_dir.glob("*/*.nmconnection"))


def test_wifi_profile_guard_restores_missing_profiles_from_backup(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    profile_dir = tmp_path / "profiles"
    backup_dir = tmp_path / "backups"
    status_file = tmp_path / "status.json"
    backup_snapshot = backup_dir / "20260630T100000Z"
    profile_dir.mkdir()
    backup_snapshot.mkdir(parents=True)
    (backup_snapshot / "greenhouse.nmconnection").write_text("[connection]\n", encoding="utf-8")
    nmcli_log = tmp_path / "nmcli.log"
    _write_fake_command(
        fake_bin,
        "nmcli",
        f"printf '%s\\n' \"$*\" >> '{nmcli_log}'\nexit 0\n",
    )
    _write_fake_command(fake_bin, "chown", "exit 0\n")

    result = _run_guard(tmp_path, fake_bin, profile_dir, backup_dir, status_file)

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert result.returncode == 0
    assert status["action"] == "restore_profiles"
    assert status["result"] == "recovered"
    assert (profile_dir / "greenhouse.nmconnection").exists()
    assert "connection reload" in nmcli_log.read_text(encoding="utf-8")
    assert "connection up greenhouse" in nmcli_log.read_text(encoding="utf-8")


def _run_guard(tmp_path: Path, fake_bin: Path, profile_dir: Path, backup_dir: Path, status_file: Path):
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "WIFI_INTERFACE": "wlan0",
            "WIFI_PROFILE_DIR": str(profile_dir),
            "WIFI_PREFERRED_PROFILE": "greenhouse",
            "WIFI_BACKUP_DIR": str(backup_dir),
            "NETWORK_RECOVERY_STATUS_FILE": str(status_file),
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _write_fake_command(fake_bin: Path, name: str, body: str) -> None:
    fake_bin.mkdir(exist_ok=True)
    command = fake_bin / name
    command.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_bash_available_for_wifi_guard_tests() -> None:
    assert shutil.which("bash") is not None
