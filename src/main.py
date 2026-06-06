"""Application entry point for the Senior Pomidor edge node."""

from __future__ import annotations

import time
from typing import Any

from src.config import ConfigError, Settings, load_config
from src.network.http_sender import HttpSender
from src.network.mqtt_sender import MqttSender
from src.sensors import adc_ads1115, air_bme280, ir_mlx90615, light_bh1750, temp_ds18b20
from src.utils.formatter import format_payload
from src.utils.local_storage import save_payload
from src.utils.logger import configure_logger


def collect_readings(settings: Settings) -> dict[str, Any]:
    return {
        "pod_1": {
            "soil_moisture": adc_ads1115.read(
                channel=settings.ads1115_pod1_channel,
                dry_voltage=settings.ads1115_pod1_dry_voltage,
                wet_voltage=settings.ads1115_pod1_wet_voltage,
                address=settings.ads1115_address,
                mock=settings.mock_sensors,
                pod_index=1,
            ),
            "air": air_bme280.read(
                address=settings.bme280_pod1_address,
                mock=settings.mock_sensors,
                pod_index=1,
            ),
            "soil_temperature": temp_ds18b20.read(
                rom_id=settings.ds18b20_pod1_rom,
                mock=settings.mock_sensors,
                pod_index=1,
            ),
        },
        "pod_2": {
            "soil_moisture": adc_ads1115.read(
                channel=settings.ads1115_pod2_channel,
                dry_voltage=settings.ads1115_pod2_dry_voltage,
                wet_voltage=settings.ads1115_pod2_wet_voltage,
                address=settings.ads1115_address,
                mock=settings.mock_sensors,
                pod_index=2,
            ),
            "air": air_bme280.read(
                address=settings.bme280_pod2_address,
                mock=settings.mock_sensors,
                pod_index=2,
            ),
            "soil_temperature": temp_ds18b20.read(
                rom_id=settings.ds18b20_pod2_rom,
                mock=settings.mock_sensors,
                pod_index=2,
            ),
        },
        "shared": {
            "light": light_bh1750.read(address=settings.bh1750_address, mock=settings.mock_sensors),
            "leaf_temperature": ir_mlx90615.read(address=settings.mlx90615_address, mock=settings.mock_sensors),
        },
    }


def run(settings: Settings) -> None:
    logger = configure_logger()
    mqtt_sender = MqttSender(settings, logger=logger)
    http_sender = HttpSender(settings, logger=logger)
    tick = 0

    logger.info("Starting Senior Pomidor edge node device_id=%s mock_sensors=%s", settings.device_id, settings.mock_sensors)
    while True:
        tick += 1
        readings = collect_readings(settings)
        payload = format_payload(settings, readings)
        save_payload(settings, payload, logger=logger)
        delivered = mqtt_sender.publish(payload)
        if not delivered and settings.http_enabled:
            http_sender.send(payload)

        if settings.max_ticks is not None and tick >= settings.max_ticks:
            logger.info("Stopping after MAX_TICKS=%s", settings.max_ticks)
            return
        time.sleep(settings.poll_interval_seconds)


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
