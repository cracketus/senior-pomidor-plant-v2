# Senior Pomidor: Edge Node

[![Quality](https://github.com/cracketus/senior-pomidor-plant-v2/actions/workflows/quality.yml/badge.svg)](https://github.com/cracketus/senior-pomidor-plant-v2/actions/workflows/quality.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Release](https://img.shields.io/github/v/release/cracketus/senior-pomidor-plant-v2?include_prereleases)](https://github.com/cracketus/senior-pomidor-plant-v2/releases)

Python edge-node software for balcony plant monitoring. The same application runs on Linux and Windows in mock mode; real sensor hardware mode is supported on Linux/Raspberry Pi.

This repository contains only the balcony hardware and telemetry collection layer. The Core AI server, database, and LLM/VLM processing live in a separate repository.

## Current Status

This `v0.1.0-alpha` release is an edge-node foundation, not the full Senior Pomidor platform.

- This repository contains the Raspberry Pi edge node only.
- Core server, database, dashboards, state estimation, AI/VLM processing, and public datasets live outside this runtime scope.
- The state-estimator documentation describes integration contracts and roadmap behavior, not code that runs in this edge node.
- Actuation and autonomous control are not included.
- Hardware mode requires Raspberry Pi Linux; Windows and general Linux development use mock sensors.

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
| BME280 | I2C | `0x76` | Shared air temperature, humidity, pressure for both pods |
| BH1750 | I2C | `0x23` | Illuminance in lux |
| MLX90615 | I2C / SMBus | `0x5A` | Non-contact leaf temperature |
| DS18B20 x2 | 1-Wire | ROM IDs from `.env` | Soil temperature |
| INA219 | I2C | `0x40` | Pod 1 hardware bus voltage and current |
| USB Camera | V4L2 | `/dev/video0`, `fswebcam` | High-resolution plant photos |

## Project Structure

```text
.
|-- docker-compose.yml
|-- docker-compose.mock.yml
|-- Dockerfile
|-- docs/
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
- `LOCAL_EVENT_DIR`: directory where queued lifecycle event JSON files are stored.
- `LOCAL_STORAGE_MAX_AGE_DAYS`: maximum age of local telemetry files before cleanup.
- `LOCAL_STORAGE_MAX_SIZE_MB`: maximum disk space used by local telemetry files.
- `CAMERA_ENABLED`: set to `true` on Raspberry Pi when the camera should capture photos.
- `CAMERA_INTERVAL_SECONDS`: delay between camera capture attempts, independent from telemetry polling.
- `CAMERA_STORAGE_DIR`: directory where accepted JPEG photos and metadata sidecars are stored.
- `CAMERA_DEVICE`, `CAMERA_RESOLUTION`, `CAMERA_JPEG_QUALITY`, `CAMERA_SKIP_FRAMES`, `CAMERA_MAX_ATTEMPTS`, `CAMERA_MIN_SHARPNESS`: USB camera capture quality and retry controls.
- `PHOTO_UPLOAD_ENABLED`, `PHOTO_UPLOAD_URL`, `PHOTO_UPLOAD_TOKEN`: optional HTTP photo upload settings.
- `INA219_ADDRESS`: health-control hardware setting for Pod 1 bus monitoring.
- `WIFI_INTERFACE`, `WIFI_PROFILE_DIR`, `WIFI_PREFERRED_PROFILE`: Raspberry Pi Wi-Fi health probe settings.
- `NETWORK_CHECK_HOST`, `NETWORK_DNS_CHECK_HOST`, `NETWORK_RECOVERY_STATUS_FILE`: network reachability and host recovery status settings.
- `DISK_USAGE_PATH`: filesystem path used by Raspberry Pi OS disk health probes.
- `ADS1115_*_DRY_READING` and `ADS1115_*_WET_READING`: raw ADS1115 soil moisture calibration values from `AnalogIn.value`.

MQTT publishes one JSON payload per tick to:

```text
{MQTT_TOPIC_PREFIX}/{DEVICE_ID}/telemetry
```

Planned maintenance lifecycle events publish to:

```text
{MQTT_TOPIC_PREFIX}/{DEVICE_ID}/events
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
        "air_saturation_vapor_pressure_kpa": 3.07,
        "air_actual_vapor_pressure_kpa": 1.79,
        "air_vpd_kpa": 1.29,
        "light_lux": 18000.0,
        "ir_ambient_temp_c": 24.8,
        "leaf_temp_c": 25.1,
        "leaf_saturation_vapor_pressure_kpa": 3.19,
        "leaf_vpd_kpa": 1.4
      },
      "errors": []
    }
  },
  "system_health": {
    "rpi_core": {
      "cpu_temp_c": 56.4,
      "wifi_rssi_dbm": -68.0,
      "disk_usage_percent": 34.2,
      "disk_free_percent": 65.8,
      "disk_total_bytes": 32000000000,
      "disk_used_bytes": 10944000000,
      "disk_free_bytes": 21056000000,
      "filesystem_read_only": false,
      "telemetry_buffer_file_count": 3,
      "telemetry_buffer_size_bytes": 12288,
      "photo_buffer_file_count": 2,
      "photo_buffer_size_bytes": 2400000,
      "recent_io_error_count": 0,
      "io_wait_percent": 1.7
    },
    "network": {
      "wifi_connected": true,
      "interface_up": true,
      "ssid": "example-wifi",
      "ip_address": "192.0.2.42",
      "default_gateway_reachable": true,
      "dns_resolution_ok": true,
      "internet_reachable": true,
      "wifi_profile_count": 1,
      "active_profile_present": true,
      "preferred_profile_present": true,
      "last_recovery_exit_code": 0
    },
    "pod_1_hardware": {
      "bus_voltage_v": 3.25,
      "bus_current_ma": 12.4
    },
    "errors": []
  }
}
```

Plant sensor errors are reported in each pod's `errors` array so partial telemetry can still be delivered. Hardware health probe errors are reported in `system_health.errors`; failed health metrics or subtrees are omitted while the rest of the health payload remains available.

Vapor pressure deficit metrics are derived on the edge node after raw sensor readings are merged into each enabled pod. Air VPD uses BME280 air temperature and relative humidity. Leaf VPD additionally uses the MLX90615/MLX90614 leaf temperature. If the required raw inputs are unavailable, the corresponding VPD fields are omitted while the rest of the pod metrics remain available.

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
./scripts/setup_raspberry_pi.sh --hardware --mqtt-host 192.0.2.10 --pod2-disabled
```

## Local Storage

Every telemetry payload is saved locally before network delivery. By default, Docker stores files on the Raspberry Pi host at:

```text
./data/telemetry
```

Retention is controlled by:

```env
LOCAL_STORAGE_DIR=data/telemetry
LOCAL_EVENT_DIR=data/events
LOCAL_STORAGE_MAX_AGE_DAYS=30
LOCAL_STORAGE_MAX_SIZE_MB=256
```

Cleanup runs after each saved payload. Files older than the configured age are removed first; if the directory still exceeds the configured size, the oldest remaining files are removed until the directory is below the limit.

## Planned Maintenance Events

Use explicit lifecycle events when intentionally shutting down the Raspberry Pi edge node for sensor service or planned maintenance. Before stopping the container or powering down the Pi, run:

```bash
python scripts/maintenance_event.py start --reason "sensor service"
```

After the Pi and edge service are back, run:

```bash
python scripts/maintenance_event.py complete --reason "sensor service"
```

The event payload uses schema version `senior-pomidor.edge.event.v1` and includes `event_id`, `device_id`, `event_type`, `timestamp_utc`, `source`, and optional `reason`. Supported event types are `maintenance_started` and `maintenance_completed`.

If the MQTT broker or Core server is unavailable, the event is queued locally under `LOCAL_EVENT_DIR` and retried the next time the maintenance event command runs.

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
PHOTO_UPLOAD_URL=http://192.0.2.10:8000/api/v1/edge/photos
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

Enable the tracked pre-commit secret guard once per clone:

```bash
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
```

The hook blocks staged local environment files, private-key files, and obvious password/token assignments before commit. If `gitleaks` is installed locally, the hook also runs `gitleaks protect --staged`.

Run the code quality harness locally:

```bash
python -m pip install -r requirements-dev.txt
ruff format --check .
ruff check .
mypy src
pip-audit --cache-dir .cache/pip-audit -r requirements.txt
pip-audit --cache-dir .cache/pip-audit -r requirements-hardware.txt
```

Shell, Docker, and secret hygiene checks used by CI:

```bash
shellcheck scripts/setup_raspberry_pi.sh
docker compose config
docker compose -f docker-compose.mock.yml config
hadolint Dockerfile
gitleaks detect --source . --no-git
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

Operations runbooks:

- [Edge architecture overview](docs/architecture.md)
- [Hardware BOM and wiring guide](docs/hardware-bom-and-wiring.md)
- [Raspberry Pi 24/7 OS configuration](docs/raspberry-pi-24-7-os.md)
- [Monthly maintenance and planned restarts](docs/maintenance-runbook.md)
- [Edge contract policy](docs/contracts.md)
- [Core integration contract](docs/core-integration.md)
- [Repository scope and prototype reference](docs/scope-and-prototype-reference.md)
- [Hardware calibration spec](docs/hardware-calibration-spec.md)

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
  --mqtt-host 192.0.2.10 \
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

Review `.env` after the first run and set the real MQTT server address, DS18B20 ROM IDs, health-control address values, and calibration values before relying on real telemetry.

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

## Hardware Discovery and Troubleshooting

Test only selected sensors without starting MQTT, storage, camera capture, or the main application loop:

```bash
docker compose build senior-pomidor-edge
docker compose run --rm --no-deps senior-pomidor-edge \
  python scripts/test_sensors.py bme280 bh1750 --repeat 3 --interval 1
```

Use `all` to test every configured sensor, or list the available names:

```bash
docker compose run --rm --no-deps senior-pomidor-edge python scripts/test_sensors.py --list
docker compose run --rm --no-deps senior-pomidor-edge python scripts/test_sensors.py all
```

The command reads sensor addresses, ADS1115 calibration, DS18B20 ROM IDs, and `MOCK_SENSORS` from `.env`. Each result is marked `ok` or `error`, and the command exits with status `1` if any selected sensor fails. Add `--mock` to verify the command without hardware.

Start with a single hardware tick and the latest saved telemetry file. This shows both numeric values and isolated sensor errors:

```bash
docker compose logs -f senior-pomidor-edge
ls -lt data/telemetry | head
cat data/telemetry/<latest-file>.json
```

In mock mode the readings are fixed example values. If real sensors are connected, confirm `.env` contains:

```env
MOCK_SENSORS=false
```

After changing `.env`, restart the container:

```bash
docker compose up --build -d
```

### Find Sensor IDs and Addresses

I2C sensors share `/dev/i2c-1`. Detect them on the Raspberry Pi host:

```bash
sudo i2cdetect -y 1
```

Expected addresses:

| Device | Expected address | `.env` setting |
| --- | --- | --- |
| ADS1115 | `0x48` | `ADS1115_ADDRESS=0x48` |
| BME280 | `0x76` | `BME280_ADDRESS=0x76` |
| BH1750 | `0x23` | `BH1750_ADDRESS=0x23` |
| MLX90615 / MLX90614-compatible | `0x5A` | `MLX90615_ADDRESS=0x5A` |
| INA219 | `0x40` | `INA219_ADDRESS=0x40` |

DS18B20 sensors are 1-Wire devices and expose ROM IDs under `/sys/bus/w1/devices`:

```bash
ls /sys/bus/w1/devices/
ls /sys/bus/w1/devices/28-*
```

Use the full `28-...` directory name in `.env`:

```env
DS18B20_POD1_ROM=28-000000000001
DS18B20_POD2_ROM=28-000000000002
```

USB cameras usually appear as `/dev/video*`:

```bash
v4l2-ctl --list-devices
ls -l /dev/video*
```

Set the selected device:

```env
CAMERA_DEVICE=/dev/video0
```

Wi-Fi health uses the host interface name:

```bash
iwconfig
cat /proc/net/wireless
```

Set it if your Raspberry Pi does not use `wlan0`:

```env
WIFI_INTERFACE=wlan0
```

NetworkManager profile health uses the host profile directory. In Docker hardware mode, `docker-compose.yml` mounts it read-only so the app can detect missing profiles without modifying host network configuration:

```env
WIFI_PROFILE_DIR=/etc/NetworkManager/system-connections
WIFI_PREFERRED_PROFILE=
NETWORK_CHECK_HOST=1.1.1.1
NETWORK_DNS_CHECK_HOST=example.com
NETWORK_RECOVERY_STATUS_FILE=data/network-recovery/status.json
```

To install the optional host-level Wi-Fi guard that backs up `.nmconnection` files and attempts recovery through NetworkManager, run setup with:

```bash
./scripts/setup_raspberry_pi.sh --hardware --install-wifi-guard
```

### If a Sensor Is Not Detected

- Reboot after enabling I2C or 1-Wire. The setup script will tell you when this is required.
- Verify I2C is enabled with `sudo raspi-config` or by checking `/boot/firmware/config.txt` for `dtparam=i2c_arm=on`.
- Verify 1-Wire is enabled by checking for `dtoverlay=w1-gpio` and `/sys/bus/w1/devices/28-*`.
- Confirm power, ground, SDA, and SCL wiring. I2C needs common ground and the correct 3.3 V logic level.
- Check for address conflicts. The single shared BME280 normally uses `0x76`; if your board is strapped to `0x77`, set `BME280_ADDRESS=0x77`.
- Run `sudo i2cdetect -y 1` on the host, not inside a broken container first. If the host cannot see the address, the app cannot read it.
- For Docker hardware mode, use `docker-compose.yml`, not `docker-compose.mock.yml`. The hardware compose file passes through I2C, camera, 1-Wire, `/sys`, and host networking.
- Rebuild after dependency changes: `docker compose up --build -d`.
- If only Pod 2 is missing, set `POD2_ENABLED=false` so the app skips Pod 2 sensors cleanly.

### If Values Look Wrong

- Check `MOCK_SENSORS`. Mock mode returns stable example values and ignores real hardware.
- ADS1115 soil moisture depends on calibration. Re-measure dry and wet raw readings and update `ADS1115_POD*_DRY_READING` and `ADS1115_POD*_WET_READING`. If moisture moves backward, dry and wet values are likely swapped.
- BME280 pressure should be near local atmospheric pressure, often around `950-1050 hPa`. A very wrong value usually means the wrong I2C address, bad wiring, or a damaged board.
- DS18B20 should be stable. If it disappears intermittently, check the 4.7 kOhm pull-up resistor between data and 3.3 V and confirm the ROM ID in `.env`.
- INA219 voltage should match the monitored bus, and current depends on correct load/shunt wiring direction. Negative or impossible current usually means the load is wired on the wrong side or the sensor is measuring the wrong rail.
- MLX90615/MLX90614 leaf temperature is line-of-sight. Reflective, wet, or off-target leaves can produce surprising values.
- Wi-Fi RSSI is in dBm. Values around `-30` are strong, around `-70` are weak but usable, and below `-80` are unreliable.
- CPU temperature is Celsius. Sustained values near throttling range mean the Pi needs better airflow, a heatsink, or lower enclosure temperature.
- VPD metrics are in kPa. `air_vpd_kpa` requires `air_temperature_c` and `air_humidity_percent`; `leaf_vpd_kpa` also requires `leaf_temp_c`. If BME280 fails, no VPD fields are emitted for that tick.
- Disk total, used, free, and percentage values come from `psutil`. `filesystem_read_only` is derived from the mount options for `DISK_USAGE_PATH`; set that path to the root filesystem or the mounted local-storage filesystem you need to monitor.
- Telemetry and photo buffer metrics recursively count regular files under `LOCAL_STORAGE_DIR` and `CAMERA_STORAGE_DIR`. Missing directories report zero files and zero bytes.
- `recent_io_error_count` counts matching MicroSD, block-device, and filesystem errors in the last hour of the kernel journal. If the journal is unavailable to the container, the probe is reported under `system_health.errors`.
- `io_wait_percent` keeps reporting the current `psutil` CPU I/O-wait measurement.
- `system_health.network.wifi_profile_count` reports stored NetworkManager Wi-Fi profiles. `0` is critical on a Wi-Fi-only edge node because NetworkManager has no saved SSID/security profile to reconnect.
- `system_health.network.last_recovery_exit_code` comes from the optional host Wi-Fi guard status file. `0` means the last guard run completed successfully; non-zero values need host-side investigation.

### Reading Error Fields

Sensor failures are non-fatal. Plant sensor errors appear under each pod:

```json
"errors": [
  { "sensor": "bme280", "message": "timeout" }
]
```

System health errors appear under `system_health.errors`:

```json
"system_health": {
  "errors": [
    { "sensor": "rpi_wifi_rssi", "message": "RSSI for interface wlan0 is unavailable" }
  ]
}
```

When an error appears, fix the named sensor first, then run one telemetry tick again and inspect the newest JSON file.

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

## Public Status Page

The tracked [index.html](index.html) is a static public status page shell for GitHub Pages or another static host. It reads a status JSON document matching [status/status.sample.json](status/status.sample.json); the sample file is documentation and test data, not live service status.

## Examples

Schema-valid example payloads are available in [examples](examples/):

- [Telemetry payload](examples/edge-telemetry-v2.example.json)
- [Lifecycle event payload](examples/edge-event-v1.example.json)
- [Photo metadata payload](examples/edge-photo-v1.example.json)
