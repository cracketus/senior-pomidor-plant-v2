"""Application entry point for the Senior Pomidor edge node."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from src.config import ConfigError, Settings, load_config, public_settings
from src.network.http_sender import HttpSender
from src.network.mqtt_sender import MqttSender
from src.network.photo_sender import HttpPhotoSender
from src.sensors import (
    adc_ads1115,
    air_bme280,
    application_health,
    ina219,
    ir_mlx90615,
    light_bh1750,
    network_health,
    rpi_core,
    temp_ds18b20,
)
from src.telemetry_spool import DeliveryWorker, SpoolError, SpoolRepository
from src.utils.camera import capture_photo
from src.utils.formatter import format_payload
from src.utils.local_storage import delete_payload_file, list_pending_payloads, load_payload_file
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
        "network": network_health.read(
            wifi_interface=settings.wifi_interface,
            wifi_profile_dir=settings.wifi_profile_dir,
            wifi_preferred_profile=settings.wifi_preferred_profile,
            network_check_host=settings.network_check_host,
            network_dns_check_host=settings.network_dns_check_host,
            recovery_status_file=settings.network_recovery_status_file,
            mqtt_host=settings.mqtt_host,
            mqtt_port=settings.mqtt_port,
            http_enabled=settings.http_enabled,
            core_http_url=settings.core_http_url,
            photo_upload_enabled=settings.photo_upload_enabled,
            photo_upload_url=settings.photo_upload_url,
            telemetry_queue_dir=settings.local_storage_dir,
            photo_queue_dir=settings.camera_storage_dir,
            timeout_seconds=settings.http_timeout_seconds,
            mock=settings.mock_sensors,
        ),
        "application": application_health.read(
            service_name=settings.service_name,
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
    repository: SpoolRepository | None = None,
    delivery_worker: DeliveryWorker | None = None,
) -> None:
    logger = configure_logger()
    mqtt_sender = MqttSender(settings, logger=logger)
    http_sender = HttpSender(settings, logger=logger)
    photo_sender = photo_sender or HttpPhotoSender(settings, logger=logger)
    repository = repository or _open_spool(settings, logger)
    repository.import_legacy(settings.local_storage_dir)
    repository.cleanup_delivered(settings.telemetry_spool_delivered_retention_days)
    worker = delivery_worker or DeliveryWorker(
        repository,
        http_sender,
        mqtt_sender,
        batch_size=settings.telemetry_spool_batch_size,
        rate_limit_per_second=settings.telemetry_spool_rate_limit_per_second,
        checkpoint_interval_seconds=settings.telemetry_spool_checkpoint_interval_seconds,
        logger=logger,
    )
    worker.start()
    next_camera_at = 0.0
    tick = 0
    last_spool_status: str | None = None

    logger.info(
        "Starting Senior Pomidor edge node device_id=%s mock_sensors=%s",
        settings.device_id,
        settings.mock_sensors,
    )
    logger.info("Effective configuration: %s", public_settings(settings))
    pending_payload: dict[str, Any] | None = None
    storage_exhausted = False
    try:
        while True:
            repository.relieve_disk_pressure(settings.telemetry_spool_delivered_retention_days)
            preflight_health = repository.health()
            if str(preflight_health.get("disk_status", "OK")) in {"DEGRADED", "CRITICAL"}:
                if not storage_exhausted:
                    logger.critical(
                        "Telemetry sampling suspended because spool disk state is %s",
                        preflight_health["disk_status"],
                    )
                    storage_exhausted = True
                sleep(min(5.0, settings.poll_interval_seconds))
                continue
            if pending_payload is None:
                readings = collect_readings(settings)
                spool_health = preflight_health
                current_status = str(spool_health["status"])
                if last_spool_status is not None and current_status != last_spool_status:
                    logger.warning("Telemetry spool state changed: %s -> %s", last_spool_status, current_status)
                last_spool_status = current_status
                readings.setdefault("system_health", {})["spool"] = spool_health
                pending_payload = format_payload(settings, readings)
            try:
                repository.enqueue(pending_payload)
            except Exception as exc:  # noqa: BLE001 - persist-before-send safety boundary
                if not storage_exhausted:
                    logger.critical("SPOOL_STORAGE_EXHAUSTED: telemetry persist failed: %s", exc)
                    storage_exhausted = True
                with suppress(Exception):
                    repository.increment_counter("write_failure_total")
                sleep(min(5.0, settings.poll_interval_seconds))
                continue
            if storage_exhausted:
                logger.info("Telemetry spool storage recovered")
                storage_exhausted = False
            pending_payload = None
            tick += 1
            worker.notify()

            if settings.camera_enabled and not storage_exhausted:
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
    finally:
        worker.stop()
        repository.close()


def _open_spool(settings: Settings, logger: Any) -> SpoolRepository:
    return SpoolRepository(
        settings.telemetry_spool_db_path,
        busy_timeout_ms=settings.telemetry_spool_busy_timeout_ms,
        capacity_mb=settings.telemetry_spool_capacity_mb,
        retry_schedule=settings.telemetry_spool_retry_schedule_seconds,
        retry_jitter=settings.telemetry_spool_retry_jitter,
        max_attempts=settings.telemetry_spool_max_attempts,
        disk_warning_percent=settings.telemetry_spool_disk_warning_percent,
        disk_degraded_percent=settings.telemetry_spool_disk_degraded_percent,
        disk_critical_percent=settings.telemetry_spool_disk_critical_percent,
        max_payload_bytes=settings.telemetry_spool_max_payload_bytes,
        poll_interval_seconds=settings.poll_interval_seconds,
        logger=logger,
    ).open()


def _replay_pending_telemetry(
    settings: Settings,
    mqtt_sender: MqttSender,
    http_sender: HttpSender,
    *,
    logger: Any,
) -> int:
    """Compatibility migration helper; the runtime imports legacy files into SQLite instead."""
    delivered_count = 0
    for path in list_pending_payloads(settings)[:TELEMETRY_REPLAY_BATCH_SIZE]:
        payload = load_payload_file(path, logger=logger)
        if payload is None:
            continue
        mqtt_sender.publish(payload)
        result = http_sender.send(payload)
        accepted = bool(result) if isinstance(result, bool) else result.status.value in {"accepted", "duplicate"}
        if not accepted:
            break
        delete_payload_file(path, logger=logger)
        delivered_count += 1
    return delivered_count


def main() -> int:
    logger = configure_logger()
    try:
        run(load_config())
        return 0
    except (ConfigError, SpoolError) as exc:
        logger.error("Startup error: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
