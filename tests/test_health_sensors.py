import sys
import types

from src.sensors import ina219, rpi_core


def test_ina219_reads_voltage_and_current(monkeypatch) -> None:
    captured = {}

    class FakeBoard:
        @staticmethod
        def I2C():
            return "i2c-bus"

    class FakeINA219:
        def __init__(self, i2c, addr):
            captured["i2c"] = i2c
            captured["address"] = addr
            self.bus_voltage = 3.246
            self.current = 12.44

    monkeypatch.setitem(sys.modules, "board", FakeBoard)
    monkeypatch.setitem(sys.modules, "adafruit_ina219", types.SimpleNamespace(INA219=FakeINA219))

    reading = ina219.read(address=0x40)

    assert reading == {"bus_voltage_v": 3.25, "bus_current_ma": 12.4}
    assert captured == {"i2c": "i2c-bus", "address": 0x40}


def test_rpi_core_parses_cpu_temperature(tmp_path) -> None:
    temp_path = tmp_path / "temp"
    temp_path.write_text("56432\n", encoding="utf-8")

    assert rpi_core.read_cpu_temp_c(temp_path) == 56.4


def test_rpi_core_parses_proc_net_wireless() -> None:
    text = """
Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   55.  -68.  -256        0      0      0      0      0        0
"""

    assert rpi_core.parse_proc_net_wireless(text, "wlan0") == -68.0
    assert rpi_core.parse_proc_net_wireless(text, "wlan1") is None


def test_rpi_core_parses_iwconfig_signal_level() -> None:
    text = "Link Quality=52/70  Signal level=-68 dBm"

    assert rpi_core.parse_iwconfig(text) == -68.0


def test_rpi_core_reads_psutil_disk_and_io_wait(monkeypatch) -> None:
    fake_psutil = types.SimpleNamespace(
        disk_usage=lambda path: types.SimpleNamespace(
            total=1000,
            used=342,
            free=658,
            percent=34.24,
        ),
        cpu_times_percent=lambda interval=None: types.SimpleNamespace(iowait=1.74),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert rpi_core.read_disk_usage("/") == {
        "disk_usage_percent": 34.2,
        "disk_free_percent": 65.8,
        "disk_total_bytes": 1000,
        "disk_used_bytes": 342,
        "disk_free_bytes": 658,
    }
    assert rpi_core.read_disk_usage_percent("/") == 34.2
    assert rpi_core.read_io_wait_percent() == 1.7


def test_rpi_core_reads_memory_metrics(monkeypatch) -> None:
    fake_psutil = types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(percent=42.54, available=512_000_000),
        swap_memory=lambda: types.SimpleNamespace(percent=3.14, free=248_000_000),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert rpi_core.read_memory_metrics() == {
        "memory_usage_percent": 42.5,
        "memory_available_bytes": 512_000_000,
        "swap_usage_percent": 3.1,
        "swap_available_bytes": 248_000_000,
    }


def test_rpi_core_parses_clear_throttling_flags() -> None:
    assert rpi_core.parse_throttled_flags("throttled=0x0\n") == {
        "under_voltage_now": False,
        "frequency_capped_now": False,
        "throttled_now": False,
        "under_voltage_seen": False,
        "frequency_capped_seen": False,
        "throttled_seen": False,
    }


def test_rpi_core_parses_current_and_historical_throttling_flags() -> None:
    assert rpi_core.parse_throttled_flags("throttled=0x70007\n") == {
        "under_voltage_now": True,
        "frequency_capped_now": True,
        "throttled_now": True,
        "under_voltage_seen": True,
        "frequency_capped_seen": True,
        "throttled_seen": True,
    }


def test_rpi_core_reports_missing_vcgencmd_as_isolated_error(monkeypatch) -> None:
    def raise_missing_vcgencmd(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "vcgencmd")

    monkeypatch.setattr(rpi_core.subprocess, "run", raise_missing_vcgencmd)

    try:
        rpi_core.read_throttling_metrics()
    except RuntimeError as exc:
        assert str(exc) == "vcgencmd is unavailable"
    else:
        raise AssertionError("Expected missing vcgencmd to raise RuntimeError")


def test_rpi_core_detects_read_only_filesystem(monkeypatch) -> None:
    fake_psutil = types.SimpleNamespace(
        disk_partitions=lambda all=True: [
            types.SimpleNamespace(mountpoint="/", opts="rw,relatime"),
            types.SimpleNamespace(mountpoint="/app/data", opts="ro,nosuid"),
        ]
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert rpi_core.read_filesystem_read_only("/app/data/telemetry") is True
    assert rpi_core.read_filesystem_read_only("/app") is False


def test_rpi_core_reports_unavailable_filesystem_probe(monkeypatch) -> None:
    fake_psutil = types.SimpleNamespace(disk_partitions=lambda all=True: [])
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    try:
        rpi_core.read_filesystem_read_only("/unmounted")
    except RuntimeError as exc:
        assert str(exc) == "Filesystem mount for /unmounted is unavailable"
    else:
        raise AssertionError("Expected unavailable filesystem probe to raise RuntimeError")


def test_rpi_core_reads_buffer_file_counts_and_sizes(tmp_path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "payload.json").write_bytes(b"1234")
    (tmp_path / "nested" / "photo.jpg").write_bytes(b"123456")

    assert rpi_core.read_buffer_metrics(str(tmp_path), "telemetry_buffer") == {
        "telemetry_buffer_file_count": 2,
        "telemetry_buffer_size_bytes": 10,
    }


def test_rpi_core_counts_recent_kernel_io_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        rpi_core.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=0,
            stdout=(
                "mmc0: timeout waiting for hardware interrupt\n"
                "blk_update_request: I/O error, dev mmcblk0\n"
                "EXT4-fs error (device mmcblk0p2): ext4_find_entry\n"
            ),
            stderr="",
        ),
    )

    assert rpi_core.read_recent_io_error_count() == 2


def test_rpi_core_treats_missing_journalctl_as_zero_recent_io_errors(monkeypatch) -> None:
    def raise_missing_journalctl(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "journalctl")

    monkeypatch.setattr(rpi_core.subprocess, "run", raise_missing_journalctl)

    assert rpi_core.read_recent_io_error_count() == 0


def test_rpi_core_keeps_partial_metrics_on_probe_failure(monkeypatch) -> None:
    monkeypatch.setattr(rpi_core, "read_cpu_temp_c", lambda: 56.4)

    def raise_rssi_error(_interface):
        raise RuntimeError("RSSI unavailable")

    monkeypatch.setattr(rpi_core, "read_wifi_rssi_dbm", raise_rssi_error)
    monkeypatch.setattr(rpi_core, "read_disk_usage", lambda _path: {"disk_usage_percent": 34.2})
    monkeypatch.setattr(
        rpi_core,
        "read_filesystem_read_only",
        lambda _path: (_ for _ in ()).throw(RuntimeError("mount unavailable")),
    )
    monkeypatch.setattr(
        rpi_core,
        "read_buffer_metrics",
        lambda _path, prefix: {
            f"{prefix}_file_count": 0,
            f"{prefix}_size_bytes": 0,
        },
    )
    monkeypatch.setattr(
        rpi_core,
        "read_recent_io_error_count",
        lambda: (_ for _ in ()).throw(RuntimeError("kernel log unavailable")),
    )
    monkeypatch.setattr(rpi_core, "read_io_wait_percent", lambda: 1.7)
    monkeypatch.setattr(rpi_core, "read_throttling_metrics", lambda: {"under_voltage_now": False})
    monkeypatch.setattr(rpi_core, "read_memory_metrics", lambda: {"memory_usage_percent": 42.5})
    monkeypatch.setattr(rpi_core, "read_load_average_metrics", lambda: {"load_average_1m": 0.42})
    monkeypatch.setattr(rpi_core, "read_uptime_seconds", lambda: 86400)

    reading = rpi_core.read(wifi_interface="wlan0", disk_usage_path="/")

    assert reading == {
        "cpu_temp_c": 56.4,
        "disk_usage_percent": 34.2,
        "telemetry_buffer_file_count": 0,
        "telemetry_buffer_size_bytes": 0,
        "photo_buffer_file_count": 0,
        "photo_buffer_size_bytes": 0,
        "io_wait_percent": 1.7,
        "under_voltage_now": False,
        "memory_usage_percent": 42.5,
        "load_average_1m": 0.42,
        "uptime_seconds": 86400,
        "errors": [
            {"sensor": "rpi_wifi_rssi", "message": "RSSI unavailable"},
            {"sensor": "rpi_filesystem_status", "message": "mount unavailable"},
            {"sensor": "rpi_recent_io_errors", "message": "kernel log unavailable"},
        ],
    }
