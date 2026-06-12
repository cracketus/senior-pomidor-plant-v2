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
        disk_usage=lambda path: types.SimpleNamespace(percent=34.24),
        cpu_times_percent=lambda interval=None: types.SimpleNamespace(iowait=1.74),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert rpi_core.read_disk_usage_percent("/") == 34.2
    assert rpi_core.read_io_wait_percent() == 1.7


def test_rpi_core_keeps_partial_metrics_on_probe_failure(monkeypatch) -> None:
    monkeypatch.setattr(rpi_core, "read_cpu_temp_c", lambda: 56.4)

    def raise_rssi_error(_interface):
        raise RuntimeError("RSSI unavailable")

    monkeypatch.setattr(rpi_core, "read_wifi_rssi_dbm", raise_rssi_error)
    monkeypatch.setattr(
        rpi_core,
        "read_disk_usage_percent",
        lambda _path: 34.2,
    )
    monkeypatch.setattr(rpi_core, "read_io_wait_percent", lambda: 1.7)

    reading = rpi_core.read(wifi_interface="wlan0", disk_usage_path="/")

    assert reading == {
        "cpu_temp_c": 56.4,
        "disk_usage_percent": 34.2,
        "io_wait_percent": 1.7,
        "errors": [{"sensor": "rpi_wifi_rssi", "message": "RSSI unavailable"}],
    }
