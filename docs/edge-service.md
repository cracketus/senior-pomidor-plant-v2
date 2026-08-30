# Edge Client systemd Service

The repository includes a systemd unit at `deploy/systemd/senior-pomidor-edge.service`.
It runs the Docker Compose edge client in the foreground so logs are written to journald under `senior-pomidor-edge`.

The unit expects:

- repository checkout: `/opt/senior-pomidor-plant-v2`
- environment file: `/opt/senior-pomidor-plant-v2/.env`
- Docker Compose service: `senior-pomidor-edge`

The Raspberry Pi host must provide `vcgencmd` from `libraspberrypi-bin`. The
hardware Compose service bind-mounts the host command into the container; the
image wrapper calls that mounted host command for the Raspberry Pi power and
throttling health probe. Rebuild the hardware image after updating the repository
so the command is available inside the container.

If your checkout lives somewhere else, edit `WorkingDirectory` before installing the unit.

## Install

From the Raspberry Pi:

```bash
sudo mkdir -p /opt
sudo git clone https://github.com/cracketus/senior-pomidor-plant-v2.git /opt/senior-pomidor-plant-v2
cd /opt/senior-pomidor-plant-v2
sudo cp .env.example .env
sudo nano .env
sudo cp deploy/systemd/senior-pomidor-edge.service /etc/systemd/system/senior-pomidor-edge.service
sudo docker compose build senior-pomidor-edge
sudo systemctl daemon-reload
sudo systemctl enable --now senior-pomidor-edge.service
```

Before first start, set at least `MQTT_HOST` and any real hardware IDs in the checkout `.env`. It is the single configuration source for Docker Compose, the edge unit, and the host watchdog. Keep `SERVICE_NAME` unset for Docker and set `WATCHDOG_SERVICE_NAME=senior-pomidor-edge.service`.

Keep `INDICATOR_ENABLED=false` during initial deployment. Enable the GPIO indicator only after completing the board-isolation, resistor, ground, and pin checks in [edge-health-indicator.md](edge-health-indicator.md). A GPIO failure is isolated to the indicator worker and is visible in `system_health.indicator`; recovery requires a service restart.

## Validate

Check service state and logs:

```bash
systemctl status senior-pomidor-edge.service
journalctl -u senior-pomidor-edge.service -f
```

Run the readiness check before enabling real hardware mode:

```bash
cd /opt/senior-pomidor-plant-v2
python scripts/hardware_readiness.py --env-file .env
```

## Update

During a maintenance window:

```bash
cd /opt/senior-pomidor-plant-v2
git fetch origin
git pull --ff-only origin main
sudo docker compose build senior-pomidor-edge
sudo systemctl restart senior-pomidor-edge.service
```

The service has `Restart=always` and `RestartSec=10`, so systemd restarts the edge client after process failure or reboot. It also waits for Docker and `network-online.target` before starting. The canonical setup additionally installs `senior-pomidor-watchdog.service`; the container reads its atomic status through `data/` and does not receive DBus, systemd, or Docker-socket access.
