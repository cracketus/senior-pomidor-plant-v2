# Hardware Calibration Spec

Use this checklist before setting `MOCK_SENSORS=false` on a Raspberry Pi deployment.

## I2C Addresses

Run on the Raspberry Pi host:

```bash
sudo i2cdetect -y 1
```

Expected addresses:

| Device | Expected address | Setting |
| --- | --- | --- |
| ADS1115 | `0x48` | `ADS1115_ADDRESS=0x48` |
| BME280 | `0x76` | `BME280_ADDRESS=0x76` |
| BH1750 | `0x23` | `BH1750_ADDRESS=0x23` |
| MLX90615 / MLX90614-compatible | `0x5A` | `MLX90615_ADDRESS=0x5A` |
| INA219 | `0x40` | `INA219_ADDRESS=0x40` |

Resolve address conflicts or missing devices before running the container in hardware mode.

## ADS1115 Soil Moisture Calibration

Record raw `AnalogIn.value` readings for each connected soil probe.

1. Put the probe in dry reference media or air and wait for the reading to stabilize.
2. Record the raw value as `ADS1115_POD*_DRY_READING`.
3. Put the probe in fully wet reference media and wait for the reading to stabilize.
4. Record the raw value as `ADS1115_POD*_WET_READING`.
5. Confirm dry and wet readings differ.
6. Run one hardware telemetry tick and verify `soil_moisture_percent` moves in the expected direction.

Default channels are:

- Pod 1: `ADS1115_POD1_CHANNEL=A0`
- Pod 2: `ADS1115_POD2_CHANNEL=A1`

If a probe reads backward, dry and wet readings are likely swapped.

## DS18B20 Pod Assignment

Enable 1-Wire and reboot if needed, then list ROM IDs:

```bash
ls /sys/bus/w1/devices/28-*
```

Assign each physical probe to a pod:

- `DS18B20_POD1_ROM=28-...`
- `DS18B20_POD2_ROM=28-...`

After assignment, warm one probe at a time and run `scripts/test_sensors.py` to confirm the pod mapping.

## Sensor Placement

- Place the BME280 where it measures shared canopy air, away from direct sun and water spray.
- Aim the MLX90615/MLX90614 at representative leaves and avoid reflective, wet, or background surfaces.
- Mount the BH1750 where it sees plant-level light rather than enclosure shadows.
- Keep soil moisture probes at consistent depth and away from pot edges.
- Mount DS18B20 probes near the root zone for their assigned pods.

## Camera Validation

Verify the selected camera device on the Raspberry Pi host:

```bash
v4l2-ctl --list-devices
fswebcam --device /dev/video0 --resolution 1920x1080 --jpeg 95 --no-banner --skip 5 test.jpg
```

Then set:

```env
CAMERA_ENABLED=true
CAMERA_DEVICE=/dev/video0
CAMERA_RESOLUTION=1920x1080
```

Run one capture cycle and confirm:

- A JPEG and JSON sidecar are created under `CAMERA_STORAGE_DIR`.
- The image frames both pods or the intended target area.
- The image is sharp enough to pass `CAMERA_MIN_SHARPNESS`.
- Lighting is representative for the expected capture schedule.

## Acceptance Checklist

Before `MOCK_SENSORS=false`:

- `sudo i2cdetect -y 1` shows all expected I2C devices.
- 1-Wire ROM IDs are assigned to the correct pods.
- ADS1115 dry/wet readings are recorded for each enabled pod.
- `POD*_ENABLED` matches connected hardware.
- `scripts/test_sensors.py all` passes or reports only understood, intentionally disabled hardware.
- One saved telemetry payload has plausible units and no unexpected sensor errors.
- Camera capture works if `CAMERA_ENABLED=true`.
- MQTT delivery reaches Core, or HTTP fallback is intentionally enabled and tested.
