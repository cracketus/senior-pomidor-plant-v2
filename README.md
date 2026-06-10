# Senior Pomidor: Edge Node

Python edge-node software for balcony plant monitoring. The same application runs on Linux and Windows in mock mode; real sensor hardware mode is supported on Linux/Raspberry Pi.

This repository contains only the balcony hardware and telemetry collection layer. The Core AI server, database, and LLM/VLM processing live in a separate repository.

## Overview

The Edge Node reads soil, air, light, leaf-temperature, and hardware health sensors, formats the readings into a Senior Pomidor telemetry payload, stores a local copy on the edge node, and publishes the payload to the Core server over MQTT. HTTP is included as an optional fallback transport. The node can also capture local USB camera photos on an independent interval and upload them to the Core server over HTTP multipart.

The application is designed around three constraints:

- Sensor failures must not stop the main loop.
- Configuration belongs in environment variables, not hardcoded source.
- Development and tests must run without Raspberry Pi hardware by enabling mock sensors.

## Platform Modes

| Platform | Supported mode | Notes |
| --- | --- | --- |
| Windows | Mock sensors | Use `MOCK_SENSORS=true`. Real I2C, SMBus, and 1-Wire sensor access is not supported natively. |
| Linux desktop/server | Mock sensors | Useful for development and integration tests. |
| Raspberry Pi Linux | Mock or real sensors | Use `MOCK_SENSORS=false` for real hardware. |
| Docker on Windows/Linux | Mock sensors | Use `docker-compose.mock.yml`; no host hardware passthrough is required. |
| Docker on Raspberry Pi Linux | Real sensors | Use `docker-compose.yml`; it passes through `/dev/i2c-1`, `/dev/video0`, and `/sys/bus/w1`. |

If `MOCK_SENSORS` is omitted, the app defaults to mock mode on non-Linux platforms and real sensor mode on Linux. Setting `MOCK_SENSORS=false` on Windows is rejected at startup with a configuration error.

## Hardware

| Sensor | Protocol | Address / Pin | Measurement |
| --- | --- | --- | --- |
| ADS1115 | I2C | `0x48`, channels `A0`, `A1` | Capacitive soil moisture raw ADC reading, calibrated to percent |
| BME280 x2 | I2C | `0x76`, `0x77` | Air temperature, humidity, pressure |
| BH1750 | I2C | `0x23` | Illuminance in lux |
| MLX90615 | I2C / SMBus | `0x5A` | Non-contact leaf temperature |
| DS18B20 x2 | 1-Wire | ROM IDs from `.env` | Soil temperature |
| DHT11 | GPIO | `DHT11_POD1_GPIO`, default `4` | Pod 1 box air temperature and humidity |
| INA219 | I2C | `0x40` | Pod 1 hardware bus voltage and current |
| USB Camera | V4L2 | `/dev/video0`, `fswebcam` | High-resolution plant photos |

## Project Structure

```text
.
|-- docker-compose.yml
|-- docker-compose.mock.yml
|-- Dockerfile
|-- requirements.txt
|-- requirements-hardware.txt
|-- scripts/
|-- .env.example
|-- data/
|-- src/
|   |-- main.py
|   |-- config.py
|   |-- sensors/
|   |-- network/
|   `-- utils/
`-- tests/
```

## Configuration

Copy `.env.example` to `.env` and adjust values for the target environment.

Important variables:

- `DEVICE_ID`: stable edge-node identifier.
- `POLL_INTERVAL_SECONDS`: delay between telemetry ticks.
- `MOCK_SENSORS`: `true` for mock mode, `false` for Raspberry Pi hardware mode.
- `POD1_ENABLED`, `POD2_ENABLED`: set a pod to `false` when it is not physically connected.
- `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC_PREFIX`: primary delivery settings.
- `HTTP_ENABLED`, `CORE_HTTP_URL`: optional fallback sender settings.
- `LOCAL_STORAGE_DIR`: directory where local telemetry JSON files are stored.
- `LOCAL_STORAGE_MAX_AGE_DAYS`: maximum age of local telemetry files before cleanup.
- `LOCAL_STORAGE_MAX_SIZE_MB`: maximum disk space used by local telemetry files.
- `CAMERA_ENABLED`: set to `true` on Raspberry Pi when the camera should capture photos.
- `CAMERA_INTERVAL_SECONDS`: delay between camera capture attempts, independent from telemetry polling.
- `CAMERA_STORAGE_DIR`: directory where accepted JPEG photos and metadata sidecars are stored.
- `CAMERA_DEVICE`, `CAMERA_RESOLUTION`, `CAMERA_JPEG_QUALITY`, `CAMERA_SKIP_FRAMES`, `CAMERA_MAX_ATTEMPTS`, `CAMERA_MIN_SHARPNESS`: USB camera capture quality and retry controls.
- `PHOTO_UPLOAD_ENABLED`, `PHOTO_UPLOAD_URL`, `PHOTO_UPLOAD_TOKEN`: optional HTTP photo upload settings.
- `DHT11_POD1_GPIO`, `INA219_ADDRESS`: health-control hardware settings for Pod 1 box climate and bus monitoring.
- `WIFI_INTERFACE`, `DISK_USAGE_PATH`: Raspberry Pi OS health probe settings.
- `ADS1115_*_DRY_READING` and `ADS1115_*_WET_READING`: raw ADS1115 soil moisture calibration values from `AnalogIn.value`.

MQTT publishes one JSON payload per tick to:

```text
{MQTT_TOPIC_PREFIX}/{DEVICE_ID}/telemetry
```

## Payload Shape

Telemetry payloads use schema version `senior-pomidor.edge.telemetry.v2`:

```json
{
  "schema_version": "senior-pomidor.edge.telemetry.v2",
  "device_id": "balcony-edge-01",
  "timestamp_utc": "2026-06-06T10:00:00Z",
  "pods": {
    "pod_1": {
      "metrics": {
        "adc_raw": 12450.0,
        "soil_moisture_percent": 45.0,
        "soil_temperature_c": 22.4,
        "air_temperature_c": 24.5,
        "air_humidity_percent": 58.2,
        "air_pressure_hpa": 1008.3,
        "light_lux": 18000.0,
        "ir_ambient_temp_c": 24.8,
        "leaf_temp_c": 25.1
      },
      "errors": []
    }
  },
  "system_health": {
    "rpi_core": {
      "cpu_temp_c": 56.4,
      "wifi_rssi_dbm": -68.0,
      "disk_usage_percent": 34.2,
      "io_wait_percent": 1.7
    },
    "pod_1_hardware": {
      "bus_voltage_v": 3.25,
      "bus_current_ma": 12.4,
      "box_climate": {
        "air_temp_c": 26.0,
        "air_humidity_percent": 45.0
      }
    },
    "errors": []
  }
}
```

Plant sensor errors are reported in each pod's `errors` array so partial telemetry can still be delivered. Hardware health probe errors are reported in `system_health.errors`; failed health metrics or subtrees are omitted while the rest of the health payload remains available.

If Pod 2 is not connected yet, set this in `.env`:

```env
POD2_ENABLED=false
```

The main loop will skip all Pod 2 sensors. The payload will still include `pods.pod_2`, but it will be marked as disabled with empty metrics and errors:

```json
{
  "enabled": false,
  "metrics": {},
  "errors": []
}
```

The Raspberry Pi setup script can set this for you:

```bash
./scripts/setup_raspberry_pi.sh --hardware --mqtt-host 192.168.1.10 --pod2-disabled
```

## Local Storage

Every telemetry payload is saved locally before network delivery. By default, Docker stores files on the Raspberry Pi host at:

```text
./data/telemetry
```

Retention is controlled by:

```env
LOCAL_STORAGE_DIR=data/telemetry
LOCAL_STORAGE_MAX_AGE_DAYS=30
LOCAL_STORAGE_MAX_SIZE_MB=256
```

Cleanup runs after each saved payload. Files older than the configured age are removed first; if the directory still exceeds the configured size, the oldest remaining files are removed until the directory is below the limit.

Camera photos are saved locally before upload. By default, Docker stores them on the Raspberry Pi host at:

```text
./data/photos
```

Each accepted photo is written as a JPEG with a JSON sidecar containing `photo_id`, `device_id`, `captured_at_utc`, file size, sharpness score, attempts, and upload status. Photo cleanup uses the same age and size limits as telemetry local storage.

## Photo Upload

Photo bytes are not sent over MQTT or embedded in telemetry payloads. The recommended Core server receive method is an HTTP multipart endpoint because photos are large binary payloads and should not share the telemetry topic.

Set:

```env
PHOTO_UPLOAD_ENABLED=true
PHOTO_UPLOAD_URL=http://192.168.1.10:8000/api/v1/edge/photos
PHOTO_UPLOAD_TOKEN=optional-bearer-token
```

The edge node sends pending photos oldest-first with:

- file field: `photo`
- form fields: `photo_id`, `device_id`, `captured_at_utc`, `schema_version=senior-pomidor.edge.photo.v1`, `sharpness_score`
- optional header: `Authorization: Bearer <PHOTO_UPLOAD_TOKEN>`

The Core server should treat `photo_id` as an idempotency key and return any 2xx status after accepting the file. On upload failure, the photo remains local with `upload_status=pending` and is retried on a later camera cycle.

## Local Development

Install common, cross-platform dependencies on Windows or Linux:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux shell equivalent:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Run tests:

```bash
pytest -q
```

Run a single mock telemetry tick on Windows PowerShell:

```powershell
$env:MQTT_HOST = "localhost"
$env:MOCK_SENSORS = "true"
$env:MAX_TICKS = "1"
python -m src.main
```

Run a single mock telemetry tick on Linux:

```bash
MQTT_HOST=localhost MOCK_SENSORS=true MAX_TICKS=1 python -m src.main
```

## Raspberry Pi Hardware Setup

The Raspberry Pi setup can be automated from the repository root:

```bash
chmod +x scripts/setup_raspberry_pi.sh
./scripts/setup_raspberry_pi.sh --hardware
```

The script installs host packages, installs Docker if needed, enables I2C and 1-Wire, creates `.env` from `.env.example`, sets `MOCK_SENSORS=false`, builds the image, and starts the hardware container.
It also installs USB camera tooling (`fswebcam` and `v4l-utils`).

If the script enables I2C or 1-Wire, it will stop and ask for a reboot. Reboot and run the same command again:

```bash
sudo reboot
cd ~/apps/senior-pomidor-plant-v2
./scripts/setup_raspberry_pi.sh --hardware
```

For a fully unattended first pass, allow automatic reboot:

```bash
./scripts/setup_raspberry_pi.sh --hardware --auto-reboot
```

You can also preseed the most important `.env` values in the same command:

```bash
./scripts/setup_raspberry_pi.sh \
  --hardware \
  --mqtt-host 192.168.1.10 \
  --device-id balcony-edge-01 \
  --pod1-rom 28-000000000001 \
  --pod2-rom 28-000000000002 \
  --interval 60 \
  --auto-reboot
```

Mock mode on Raspberry Pi uses the same setup script without hardware passthrough:

```bash
./scripts/setup_raspberry_pi.sh --mock
```

Review `.env` after the first run and set the real MQTT server address, DS18B20 ROM IDs, health-control GPIO/address values, and calibration values before relying on real telemetry.

Before enabling camera capture in the edge node, verify the camera directly on the Raspberry Pi:

```bash
fswebcam --device /dev/video0 --resolution 1920x1080 --jpeg 95 --no-banner --skip 5 test.jpg
```

Then set:

```env
CAMERA_ENABLED=true
CAMERA_INTERVAL_SECONDS=3600
CAMERA_DEVICE=/dev/video0
CAMERA_RESOLUTION=1920x1080
```

## Docker

Cross-platform mock container:

```bash
docker compose -f docker-compose.mock.yml up --build
```

The mock compose file installs only `requirements.txt`, so it does not need Raspberry Pi sensor libraries.

Raspberry Pi hardware container:

```bash
cp .env.example .env
docker compose up --build -d
```

The hardware compose file can be parsed without `.env`, but the app still requires real MQTT and sensor configuration at runtime. It installs `requirements-hardware.txt`, including `rpi-lgpio` for the `RPi.GPIO` compatibility module on Raspberry Pi OS Bookworm, persists telemetry and photos to `./data`, and is Linux/Raspberry Pi specific because it passes through hardware host paths. Camera-enabled Docker deployments use `/dev/video0` by default; the setup script installs `fswebcam`, `v4l-utils`, `libgpiod2`, and `wireless-tools`, and the compose file runs privileged with host networking plus `/run/udev` mounted for Raspberry Pi hardware and Wi-Fi RSSI access.
