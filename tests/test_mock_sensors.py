import sys
import types

from src.sensors import adc_ads1115, air_bme280, ir_mlx90615, light_bh1750, temp_ds18b20


def test_mock_sensors_return_metrics() -> None:
    soil = adc_ads1115.read("A0", dry_reading=17736, wet_reading=7220, mock=True, pod_index=1)
    air = air_bme280.read(address=0x76, mock=True, pod_index=1)
    soil_temp = temp_ds18b20.read(rom_id=None, mock=True, pod_index=1)
    light = light_bh1750.read(mock=True)
    leaf = ir_mlx90615.read(mock=True)

    assert "soil_moisture_percent" in soil
    assert "adc_raw" in soil
    assert "air_temperature_c" in air
    assert "soil_temperature_c" in soil_temp
    assert light == {"light_lux": 18500.0}
    assert leaf == {"ir_ambient_temp_c": 23.7, "leaf_temp_c": 24.9}


def test_mlx90614_reads_ambient_and_object_temperatures(monkeypatch) -> None:
    captured = {}

    class FakeBoard:
        @staticmethod
        def I2C():
            return "i2c-bus"

    class FakeMLX90614:
        def __init__(self, i2c, address):
            captured["i2c"] = i2c
            captured["address"] = address
            self.ambient_temperature = 21.234
            self.object_temperature = 24.567

    monkeypatch.setitem(sys.modules, "board", FakeBoard)
    monkeypatch.setitem(sys.modules, "adafruit_mlx90614", types.SimpleNamespace(MLX90614=FakeMLX90614))

    reading = ir_mlx90615.read(address=0x5A)

    assert reading == {"ir_ambient_temp_c": 21.23, "leaf_temp_c": 24.57}
    assert captured == {"i2c": "i2c-bus", "address": 0x5A}


def test_ads1115_calibration_clamps_values() -> None:
    assert adc_ads1115.calibrate_moisture(19000, dry_reading=17736, wet_reading=7220) == 0.0
    assert adc_ads1115.calibrate_moisture(7000, dry_reading=17736, wet_reading=7220) == 100.0


def test_ds18b20_accepts_linux_rom_directory_id(monkeypatch) -> None:
    captured = {}

    class FakeSensor:
        DS18B20 = object()

    class FakeW1ThermSensor:
        def __init__(self, sensor_type, sensor_id):
            captured["sensor_type"] = sensor_type
            captured["sensor_id"] = sensor_id

        def get_temperature(self):
            return 22.456

    fake_module = types.SimpleNamespace(Sensor=FakeSensor, W1ThermSensor=FakeW1ThermSensor)
    monkeypatch.setitem(sys.modules, "w1thermsensor", fake_module)

    reading = temp_ds18b20.read(rom_id="28-01155392a9ff")

    assert reading == {"soil_temperature_c": 22.46}
    assert captured == {"sensor_type": FakeSensor.DS18B20, "sensor_id": "01155392a9ff"}


def test_ds18b20_accepts_w1thermsensor_hardware_id(monkeypatch) -> None:
    captured = {}

    class FakeSensor:
        DS18B20 = object()

    class FakeW1ThermSensor:
        def __init__(self, sensor_type, sensor_id):
            captured["sensor_type"] = sensor_type
            captured["sensor_id"] = sensor_id

        def get_temperature(self):
            return 22.0

    fake_module = types.SimpleNamespace(Sensor=FakeSensor, W1ThermSensor=FakeW1ThermSensor)
    monkeypatch.setitem(sys.modules, "w1thermsensor", fake_module)

    reading = temp_ds18b20.read(rom_id="01155392a9ff")

    assert reading == {"soil_temperature_c": 22.0}
    assert captured == {"sensor_type": FakeSensor.DS18B20, "sensor_id": "01155392a9ff"}
