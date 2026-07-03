import subprocess
import sys
import types

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


def test_application_health_degrades_when_systemd_is_missing(monkeypatch) -> None:
    def raise_missing_systemctl(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "systemctl")

    monkeypatch.setattr(application_health.subprocess, "run", raise_missing_systemctl)

    assert application_health.read_systemd_service_metrics("senior-pomidor-edge") == {
        "systemd_service_name": "senior-pomidor-edge",
        "systemd_available": False,
    }
