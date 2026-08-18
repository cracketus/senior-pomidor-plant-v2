# Edge Client systemd Service

The repository includes a systemd unit at `deploy/systemd/senior-pomidor-edge.service`.
It runs the Docker Compose edge client in the foreground so logs are written to journald under `senior-pomidor-edge`.

The unit expects:

- repository checkout: `/opt/senior-pomidor-plant-v2`
- optional environment file: `/etc/senior-pomidor/edge.env`
- Docker Compose service: `senior-pomidor-edge`

If your checkout lives somewhere else, edit `WorkingDirectory` before installing the unit.

## Install

From the Raspberry Pi:

```bash
sudo mkdir -p /opt /etc/senior-pomidor
sudo git clone https://github.com/cracketus/senior-pomidor-plant-v2.git /opt/senior-pomidor-plant-v2
cd /opt/senior-pomidor-plant-v2
sudo cp .env.example /etc/senior-pomidor/edge.env
sudo nano /etc/senior-pomidor/edge.env
sudo cp /etc/senior-pomidor/edge.env .env
sudo cp deploy/systemd/senior-pomidor-edge.service /etc/systemd/system/senior-pomidor-edge.service
sudo docker compose build senior-pomidor-edge
sudo systemctl daemon-reload
sudo systemctl enable --now senior-pomidor-edge.service
```

Before first start, set at least `MQTT_HOST` and any real hardware IDs in `/etc/senior-pomidor/edge.env`.
Keep `/opt/senior-pomidor-plant-v2/.env` synchronized with that file because Docker Compose reads `.env` from the project directory.

## Validate

Check service state and logs:

```bash
systemctl status senior-pomidor-edge.service
journalctl -u senior-pomidor-edge.service -f
```

Run the readiness check before enabling real hardware mode:

```bash
cd /opt/senior-pomidor-plant-v2
python scripts/hardware_readiness.py --env-file /etc/senior-pomidor/edge.env
```

## Update

During a maintenance window:

```bash
cd /opt/senior-pomidor-plant-v2
git fetch origin
git pull --ff-only origin main
sudo cp /etc/senior-pomidor/edge.env .env
sudo docker compose build senior-pomidor-edge
sudo systemctl restart senior-pomidor-edge.service
```

The service has `Restart=always` and `RestartSec=10`, so systemd restarts the edge client after process failure or reboot.
It also waits for Docker and `network-online.target` before starting.
