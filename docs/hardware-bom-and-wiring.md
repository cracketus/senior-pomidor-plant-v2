# Hardware BOM and Wiring Guide

This guide summarizes the expected Raspberry Pi hardware for the alpha edge-node release. Confirm pinouts against the exact boards in use before powering hardware.

## Bill of Materials

| Component | Purpose |
| --- | --- |
| Raspberry Pi with Raspberry Pi OS | Edge runtime host |
| ADS1115 | Analog soil moisture channels |
| Capacitive soil moisture sensors | Pod soil moisture |
| BME280 | Shared air temperature, humidity, and pressure |
| BH1750 | Illuminance |
| MLX90615 or MLX90614-compatible IR sensor | Leaf temperature |
| DS18B20 waterproof probes | Soil temperature per pod |
| INA219 | Pod 1 bus voltage and current health |
| USB camera | Plant photos |
| 4.7 kOhm resistor | DS18B20 1-Wire pull-up |

## Shared I2C Bus

Use Raspberry Pi 3.3 V logic. Connect all I2C devices to common ground, SDA, and SCL.

| Device | Default address |
| --- | --- |
| ADS1115 | `0x48` |
| BME280 | `0x76` |
| BH1750 | `0x23` |
| MLX90615 / MLX90614-compatible | `0x5A` |
| INA219 | `0x40` |

Verify addresses on the Raspberry Pi host:

```bash
sudo i2cdetect -y 1
```

## 1-Wire Soil Temperature

Connect DS18B20 data to the configured 1-Wire GPIO and add a 4.7 kOhm pull-up from data to 3.3 V. Find ROM IDs with:

```bash
ls /sys/bus/w1/devices/28-*
```

Set the IDs in `.env`:

```env
DS18B20_POD1_ROM=28-000000000001
DS18B20_POD2_ROM=28-000000000002
```

## Camera

USB cameras usually appear as `/dev/video0`. Verify capture before enabling the edge camera loop:

```bash
fswebcam --device /dev/video0 --resolution 1920x1080 --jpeg 95 --no-banner --skip 5 test.jpg
```

## Calibration

Soil moisture calibration values are raw ADS1115 readings from `AnalogIn.value`, not volts. Follow [hardware-calibration-spec.md](hardware-calibration-spec.md) before relying on real moisture percentages.
