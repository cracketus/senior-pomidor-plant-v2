# Raspberry Pi 24/7 OS Runbook

This runbook configures the Senior Pomidor edge Raspberry Pi for unattended operation. It assumes Raspberry Pi OS Lite, a headless node, Ethernet as the primary network, Wi-Fi as fallback, microSD storage today, and a future move to SSD or NVMe.

The edge container already has `restart: unless-stopped` in `docker-compose.yml`. The OS configuration below makes sure Docker, networking, time sync, hardware interfaces, and recovery behavior support that restart policy.

Official references:

- Raspberry Pi configuration and SSH: https://www.raspberrypi.com/documentation/computers/configuration.html
- Raspberry Pi bootloader watchdog: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#BOOT_WATCHDOG_TIMEOUT

## Baseline

Install Raspberry Pi OS Lite 64-bit with Raspberry Pi Imager. In the advanced options, configure:

- Hostname: `senior-pomidor-edge`
- SSH enabled with key-based access
- Correct timezone, locale, and keyboard
- Wi-Fi credentials for fallback access

Use Ethernet whenever possible. Keep Wi-Fi configured as backup or management access, not as the preferred production path.

After the first boot:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

## Host Services

Enable SSH and time synchronization:

```bash
sudo systemctl enable --now ssh
sudo timedatectl set-ntp true
timedatectl status
```

Disable host sleep and suspend targets:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
systemctl status sleep.target suspend.target hibernate.target hybrid-sleep.target
```

Enable Docker at boot:

```bash
sudo systemctl enable --now docker
docker compose version
```

## Wi-Fi Fallback

Disable Wi-Fi power save for the current boot:

```bash
sudo iw dev wlan0 set power_save off
iw dev wlan0 get power_save
```

Make the setting persistent:

```bash
sudo tee /etc/systemd/system/wifi-powersave-off.service >/dev/null <<'EOF'
[Unit]
Description=Disable Wi-Fi power save
After=network.target

[Service]
Type=oneshot
ExecStart=/sbin/iw dev wlan0 set power_save off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now wifi-powersave-off.service
systemctl status wifi-powersave-off.service
```

If the interface name is not `wlan0`, update both the service file and `WIFI_INTERFACE` in `.env`.

## Edge Setup

Clone or copy this repository to the Raspberry Pi, then run the existing hardware setup from the repository root:

```bash
chmod +x scripts/setup_raspberry_pi.sh
./scripts/setup_raspberry_pi.sh \
  --hardware \
  --mqtt-host 192.168.1.10 \
  --device-id balcony-edge-01 \
  --auto-reboot
```

The script installs host packages, installs Docker when missing, enables I2C and 1-Wire, creates `.env` from `.env.example`, sets `MOCK_SENSORS=false`, builds the Docker image, and starts `senior-pomidor-edge`.

If hardware interfaces were enabled during the run, a reboot is required before they appear. After reboot, run the setup command again from the repository root.

## Firewall

Enable a default-deny firewall while preserving SSH access:

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw enable
sudo ufw status verbose
```

Open only additional ports that the edge host itself must accept. MQTT is normally outbound from the edge node to the Core server, so no inbound MQTT rule is required for the edge node.

## Docker Log Rotation

Limit container log growth on microSD:

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "5"
  }
}
EOF

sudo systemctl restart docker
docker compose up -d senior-pomidor-edge
```

This restarts Docker once. Do it during setup or during a planned maintenance window.

## Updates And Reboots

Do not enable routine automatic reboots. Security updates are useful, but reboots should happen during a planned maintenance window:

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure unattended-upgrades
```

Confirm automatic rebooting is disabled:

```bash
grep -R "Automatic-Reboot" /etc/apt/apt.conf.d/
```

The expected policy is:

```text
Unattended-Upgrade::Automatic-Reboot "false";
```

Use `docs/maintenance-runbook.md` for the monthly update and restart flow.

## Watchdog Recovery

The target is no routine rebooting, not no recovery. A watchdog is acceptable for true host hangs where SSH, Docker, and the application stop responding.

Enable the OS runtime watchdog in systemd:

```bash
sudo cp /etc/systemd/system.conf /etc/systemd/system.conf.bak
sudo nano /etc/systemd/system.conf
```

Set:

```ini
RuntimeWatchdogSec=30
RebootWatchdogSec=10min
```

Apply with a planned reboot:

```bash
sudo reboot
```

For failed-boot recovery, configure the Raspberry Pi bootloader watchdog only if the node is remote enough that manual recovery is difficult. The Raspberry Pi bootloader `BOOT_WATCHDOG_TIMEOUT` setting monitors boot only until the Arm CPU starts; it does not monitor the OS after handoff.

## Storage

For microSD:

- Use a high-endurance card.
- Keep Docker logs rotated.
- Keep local telemetry and photos under `./data`.
- Monitor disk usage monthly.

For future SSD or NVMe:

- Move the repository and `./data` to SSD/NVMe.
- Consider moving Docker's data root to SSD/NVMe before enabling long photo retention.
- Keep a tested backup of `.env` and any calibration notes before migration.

## Validation

After setup or a planned reboot:

```bash
docker ps
docker compose logs --tail=100 senior-pomidor-edge
ls /dev/i2c-1
ls /sys/bus/w1/devices
ls -lt data/telemetry | head
df -h /
systemctl is-enabled docker
systemctl status wifi-powersave-off.service
```

Confirm the node resumes telemetry after unplugging and reconnecting Ethernet. If Wi-Fi fallback is required, confirm the Pi remains reachable over Wi-Fi while Ethernet is disconnected.
