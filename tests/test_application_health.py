import subprocess
import sys
import types

import pytest

from src.sensors import application_health


def test_application_health_reads_process_metrics(monkeypatch) -> None:
    class FakeProcess:
        pid = 4321

        def is_running(self):
            return True

        def create_time(self):
            return 90.0

        def memory_info(self):
            return types.SimpleNamespace(rss=123456)

        def cpu_percent(self, interval=None):
            return 1.24

    fake_psutil = types.SimpleNamespace(Process=FakeProcess)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(application_health.time, "time", lambda: 100.0)

    assert application_health.read_process_metrics() == {
        "process_id": 4321,
        "process_running": True,
        "process_uptime_seconds": 10,
        "process_memory_rss_bytes": 123456,
        "process_cpu_percent": 1.2,
    }


def test_application_health_parses_systemd_service(monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        assert command[:3] == ["systemctl", "show", "senior-pomidor-edge"]
        assert _kwargs["timeout"] == 2
        assert _kwargs["check"] is False
        return subprocess.CompletedProcess(
            command,
            0,
            "ActiveState=active\nSubState=running\nMainPID=4321\n",
            "",
        )

    monkeypatch.setattr(application_health.subprocess, "run", fake_run)

    assert application_health.read_systemd_service_metrics("senior-pomidor-edge") == {
        "systemd_service_name": "senior-pomidor-edge",
        "systemd_available": True,
        "systemd_active_state": "active",
        "systemd_service_active": True,
        "systemd_sub_state": "running",
        "systemd_main_pid": 4321,
    }


def test_application_health_without_supervisor_keeps_process_metrics_only(monkeypatch) -> None:
    monkeypatch.setattr(
        application_health,
        "read_process_metrics",
        lambda: {"process_running": True, "process_uptime_seconds": 42, "process_memory_rss_bytes": 123},
    )

    result = application_health.read()

    assert result["process_running"] is True
    assert result["process_uptime_seconds"] == 42
    assert "systemd_available" not in result
    assert "systemd_service_name" not in result


def test_application_health_reports_missing_systemctl_as_probe_error(monkeypatch) -> None:
    def raise_missing_systemctl(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "systemctl")

    monkeypatch.setattr(application_health.subprocess, "run", raise_missing_systemctl)

    assert application_health.read_systemd_service_metrics("senior-pomidor-edge") == {
        "systemd_service_name": "senior-pomidor-edge",
        "systemd_available": False,
        "errors": [{"sensor": "application_systemd", "message": "systemctl executable not found"}],
    }


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (PermissionError("denied"), "systemctl probe could not be started: denied"),
        (OSError("dbus unavailable"), "systemctl probe could not be started: dbus unavailable"),
        (
            subprocess.TimeoutExpired(["systemctl"], 2),
            "systemctl probe timed out after 2 seconds",
        ),
    ],
)
def test_application_health_isolates_systemctl_start_failures(monkeypatch, exception, message) -> None:
    monkeypatch.setattr(
        application_health.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(exception),
    )

    result = application_health.read_systemd_service_metrics("senior-pomidor-edge")

    assert result["systemd_available"] is False
    assert result["errors"] == [{"sensor": "application_systemd", "message": message}]


def test_application_health_isolates_nonzero_systemctl_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        application_health.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 4, "", "Unit not found"),
    )

    result = application_health.read_systemd_service_metrics("missing.service")

    assert result["systemd_available"] is False
    assert result["errors"][0]["message"] == "systemctl exited with code 4: Unit not found"


@pytest.mark.parametrize(
    "output",
    [
        "not-properties\n",
        "ActiveState=active\nSubState=running\n",
        "ActiveState=active\nSubState=running\nMainPID=invalid\n",
    ],
)
def test_application_health_isolates_malformed_systemctl_output(monkeypatch, output) -> None:
    monkeypatch.setattr(
        application_health.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, output, ""),
    )

    result = application_health.read_systemd_service_metrics("senior-pomidor-edge")

    assert result["systemd_available"] is True
    assert result["errors"][0]["message"] == "systemctl returned malformed service properties"


def test_systemd_probe_error_does_not_remove_process_metrics(monkeypatch) -> None:
    monkeypatch.setattr(
        application_health,
        "read_process_metrics",
        lambda: {"process_running": True, "process_uptime_seconds": 42, "process_memory_rss_bytes": 123},
    )
    monkeypatch.setattr(
        application_health.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("systemctl")),
    )

    result = application_health.read(service_name="senior-pomidor-edge.service")

    assert result["process_running"] is True
    assert result["process_uptime_seconds"] == 42
    assert result["errors"][0]["sensor"] == "application_systemd"
