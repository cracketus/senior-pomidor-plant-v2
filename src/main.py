"""Application entry point for the Senior Pomidor edge node."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.config import ConfigError, Settings, load_config
from src.network.http_sender import HttpSender
from src.network.mqtt_sender import MqttSender
from src.network.photo_sender import HttpPhotoSender
from src.sensors import adc_ads1115, air_bme280, ina219, ir_mlx90615, light_bh1750, rpi_core, temp_ds18b20
from src.utils.camera import capture_photo
from src.utils.formatter import format_payload
from src.utils.local_storage import delete_payload_file, list_pending_payloads, load_payload_file, save_payload
from src.utils.logger import configure_logger

TELEMETRY_REPLAY_BATCH_SIZE = 10


def collect_readings(settings: Settings) -> dict[str, Any]:
    return {
        "pod_1": _collect_pod_readings(settings, pod_index=1) if settings.pod1_enabled else None,
        "pod_2": _collect_pod_readings(settings, pod_index=2) if settings.pod2_enabled else None,
        "shared": {
            "air": air_bme280.read(address=settings.bme280_address, mock=settings.mock_sensors),
            "light": light_bh1750.read(address=settings.bh1750_address, mock=settings.mock_sensors),
            "leaf_temperature": ir_mlx90615.read(address=settings.mlx90615_address, mock=settings.mock_sensors),
        },
        "system_health": _collect_system_health(settings),
    }


def _collect_pod_readings(settings: Settings, pod_index: int) -> dict[str, Any]:
    if pod_index == 1:
        channel = settings.ads1115_pod1_channel
        dry_reading = settings.ads1115_pod1_dry_reading
        wet_reading = settings.ads1115_pod1_wet_reading
        ds18b20_rom = settings.ds18b20_pod1_rom
    else:
        channel = settings.ads1115_pod2_channel
        dry_reading = settings.ads1115_pod2_dry_reading
        wet_reading = settings.ads1115_pod2_wet_reading
        ds18b20_rom = settings.ds18b20_pod2_rom

    return {
        "soil_moisture": adc_ads1115.read(
            channel=channel,
            dry_reading=dry_reading,
            wet_reading=wet_reading,
            address=settings.ads1115_address,
            mock=settings.mock_sensors,
            pod_index=pod_index,
        ),
        "soil_temperature": temp_ds18b20.read(
            rom_id=ds18b20_rom,
            mock=settings.mock_sensors,
            pod_index=pod_index,
        ),
    }


def _collect_system_health(settings: Settings) -> dict[str, Any]:
    return {
        "rpi_core": rpi_core.read(
            wifi_interface=settings.wifi_interface,
            disk_usage_path=settings.disk_usage_path,
            telemetry_buffer_path=settings.local_storage_dir,
            photo_buffer_path=settings.camera_storage_dir,
            mock=settings.mock_sensors,
        ),
        "pod_1_hardware": {
            "ina219": ina219.read(address=settings.ina219_address, mock=settings.mock_sensors),
        },
    }


def run(
    settings: Settings,
    *,
    camera_capture: Callable[..., Any] = capture_photo,
    photo_sender: HttpPhotoSender | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    logger = configure_logger()
    mqtt_sender = MqttSender(settings, logger=logger)
    http_sender = HttpSender(settings, logger=logger)
    photo_sender = photo_sender or HttpPhotoSender(settings, logger=logger)
    next_camera_at = 0.0
    tick = 0

    logger.info(
        "Starting Senior Pomidor edge node device_id=%s mock_sensors=%s",
        settings.device_id,
        settings.mock_sensors,
    )
    while True:
        tick += 1
        _replay_pending_telemetry(settings, mqtt_sender, http_sender, logger=logger)
        readings = collect_readings(settings)
        payload = format_payload(settings, readings)
        saved_path = save_payload(settings, payload, logger=logger)
        delivered = _deliver_telemetry(settings, payload, mqtt_sender, http_sender)
        if delivered and saved_path is not None:
            delete_payload_file(saved_path, logger=logger)

        if settings.camera_enabled:
            now = monotonic()
            if now >= next_camera_at:
                try:
                    camera_capture(settings, logger=logger)
                except Exception as exc:  # noqa: BLE001 - camera isolation boundary
                    logger.error("Camera capture failed unexpectedly: %s", exc)
                if settings.photo_upload_enabled:
                    try:
                        photo_sender.send_pending()
                    except Exception as exc:  # noqa: BLE001 - transport isolation boundary
                        logger.error("Photo upload failed unexpectedly: %s", exc)
                next_camera_at = now + settings.camera_interval_seconds

        if settings.max_ticks is not None and tick >= settings.max_ticks:
            logger.info("Stopping after MAX_TICKS=%s", settings.max_ticks)
            return
        sleep(settings.poll_interval_seconds)


def _replay_pending_telemetry(
    settings: Settings,
    mqtt_sender: MqttSender,
    http_sender: HttpSender,
    *,
    logger: Any,
) -> int:
    delivered_count = 0
    for path in list_pending_payloads(settings)[:TELEMETRY_REPLAY_BATCH_SIZE]:
        payload = load_payload_file(path, logger=logger)
        if payload is None:
            continue
        if not _deliver_telemetry(settings, payload, mqtt_sender, http_sender):
            logger.error("Queued telemetry delivery failed; replay will retry later: %s", path)
            break
        delete_payload_file(path, logger=logger)
        delivered_count += 1
    return delivered_count


def _deliver_telemetry(
    settings: Settings,
    payload: dict[str, Any],
    mqtt_sender: MqttSender,
    http_sender: HttpSender,
) -> bool:
    delivered = mqtt_sender.publish(payload)
    if not delivered and settings.http_enabled:
        delivered = http_sender.send(payload)
    return delivered


def main() -> int:
    logger = configure_logger()
    try:
        run(load_config())
        return 0
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
