import json
import subprocess
from pathlib import Path

from scripts import hardware_readiness
from src.config import load_config


def test_readiness_checks_pass_for_configured_hardware(tmp_path) -> None:
    _make_hardware_tree(tmp_path)
    settings = load_config(
        {
            "MQTT_HOST": "mqtt.local",
            "CORE_HTTP_URL": "http://core.local:8000/api/v1/edge/telemetry",
            "CAMERA_ENABLED": "true",
            "CAMERA_DEVICE": "/dev/video0",
            "DS18B20_POD1_ROM": "28-000000000001",
            "DS18B20_POD2_ROM": "28-000000000002",
            "MOCK_SENSORS": "false",
        },
        platform_name="Linux",
    )

    results = hardware_readiness.run_checks(
        settings,
        root=tmp_path,
        runner=FakeRunner(
            {
                ("sh", "-c", "command -v v4l2-ctl"): subprocess.CompletedProcess([], 0, "/usr/bin/v4l2-ctl\n", ""),
                ("v4l2-ctl", "--device", "/dev/video0", "--all"): subprocess.CompletedProcess([], 0, "ok", ""),
                ("timedatectl", "show", "-p", "NTPSynchronized", "--value"): subprocess.CompletedProcess(
                    [], 0, "yes\n", ""
                ),
            }
        ),
        connector=FakeConnector(),
    )

    assert {result.name: result.status for result in results} == {
        "camera": "ok",
        "i2c": "ok",
        "one_wire": "ok",
        "mqtt": "ok",
        "server_api": "ok",
        "time_sync": "ok",
    }


def test_readiness_reports_actionable_hardware_failures(tmp_path) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "mqtt.local",
            "CORE_HTTP_URL": "http://core.local:8000/api/v1/edge/telemetry",
            "CAMERA_ENABLED": "true",
            "CAMERA_DEVICE": "/dev/video0",
            "DS18B20_POD1_ROM": "28-000000000001",
            "MOCK_SENSORS": "false",
        },
        platform_name="Linux",
    )

    results = hardware_readiness.run_checks(
        settings,
        root=tmp_path,
        runner=FakeRunner(
            {
                ("timedatectl", "show", "-p", "NTPSynchronized", "--value"): subprocess.CompletedProcess(
                    [], 0, "no\n", ""
                ),
            }
        ),
        connector=FakeConnector(failing_hosts={"mqtt.local"}),
    )

    by_name = {result.name: result for result in results}
    assert by_name["camera"].status == "fail"
    assert "CAMERA_DEVICE" in by_name["camera"].remediation
    assert by_name["i2c"].status == "fail"
    assert "dtparam=i2c_arm=on" in by_name["i2c"].remediation
    assert by_name["one_wire"].status == "fail"
    assert "dtoverlay=w1-gpio" in by_name["one_wire"].remediation
    assert by_name["mqtt"].status == "fail"
    assert by_name["time_sync"].status == "fail"


def test_readiness_skips_hardware_checks_in_mock_mode(tmp_path) -> None:
    settings = load_config(
        {
            "MQTT_HOST": "mqtt.local",
            "CORE_HTTP_URL": "http://core.local:8000/api/v1/edge/telemetry",
            "MOCK_SENSORS": "true",
            "CAMERA_ENABLED": "false",
        },
        platform_name="Linux",
    )

    results = hardware_readiness.run_checks(
        settings,
        root=tmp_path,
        runner=FakeRunner(
            {
                ("timedatectl", "show", "-p", "NTPSynchronized", "--value"): subprocess.CompletedProcess(
                    [], 0, "yes\n", ""
                ),
            }
        ),
        connector=FakeConnector(),
    )

    by_name = {result.name: result for result in results}
    assert by_name["camera"].status == "ok"
    assert "skipped" in by_name["camera"].message
    assert by_name["i2c"].status == "ok"
    assert "skipped" in by_name["i2c"].message
    assert by_name["one_wire"].status == "ok"
    assert "skipped" in by_name["one_wire"].message


def test_readiness_prints_json(capsys) -> None:
    hardware_readiness.print_results(
        [hardware_readiness.CheckResult("mqtt", "ok", "Connected.")],
        json_output=True,
    )

    output = json.loads(capsys.readouterr().out)
    assert output == {"checks": [{"message": "Connected.", "name": "mqtt", "remediation": "", "status": "ok"}]}


def test_read_env_file_parses_simple_dotenv(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nMQTT_HOST=core.local\nDEVICE_ID='edge-01'\nHTTP_ENABLED=true\n",
        encoding="utf-8",
    )

    assert hardware_readiness.read_env_file(env_file) == {
        "MQTT_HOST": "core.local",
        "DEVICE_ID": "edge-01",
        "HTTP_ENABLED": "true",
    }


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses

    def __call__(self, command, _timeout):
        return self.responses.get(tuple(command), subprocess.CompletedProcess(command, 127, "", "missing"))


class FakeConnector:
    def __init__(self, failing_hosts=None):
        self.failing_hosts = set(failing_hosts or [])

    def __call__(self, address, _timeout):
        host, _port = address
        if host in self.failing_hosts:
            raise OSError("unreachable")
        return FakeConnection()


class FakeConnection:
    def close(self):
        return None


def _make_hardware_tree(root: Path) -> None:
    (root / "dev").mkdir()
    (root / "dev/i2c-1").write_text("", encoding="utf-8")
    (root / "dev/video0").write_text("", encoding="utf-8")
    (root / "sys/bus/w1/devices/28-000000000001").mkdir(parents=True)
    (root / "sys/bus/w1/devices/28-000000000002").mkdir(parents=True)
