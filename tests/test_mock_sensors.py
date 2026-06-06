from src.sensors import adc_ads1115, air_bme280, ir_mlx90615, light_bh1750, temp_ds18b20


def test_mock_sensors_return_metrics() -> None:
    soil = adc_ads1115.read("A0", dry_voltage=3.0, wet_voltage=1.2, mock=True, pod_index=1)
    air = air_bme280.read(address=0x76, mock=True, pod_index=1)
    soil_temp = temp_ds18b20.read(rom_id=None, mock=True, pod_index=1)
    light = light_bh1750.read(mock=True)
    leaf = ir_mlx90615.read(mock=True)

    assert "soil_moisture_percent" in soil
    assert "air_temperature_c" in air
    assert "soil_temperature_c" in soil_temp
    assert light == {"light_lux": 18500.0}
    assert leaf == {"leaf_temperature_c": 24.9}


def test_ads1115_calibration_clamps_values() -> None:
    assert adc_ads1115.calibrate_moisture(3.5, dry_voltage=3.0, wet_voltage=1.2) == 0.0
    assert adc_ads1115.calibrate_moisture(0.8, dry_voltage=3.0, wet_voltage=1.2) == 100.0
