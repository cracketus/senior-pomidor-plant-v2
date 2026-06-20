# Maintenance Runbook

Use this runbook for planned Senior Pomidor edge maintenance. The default cadence is monthly, with a short maintenance window where restarts are allowed.

Routine operation should avoid automatic reboots. Planned restarts are acceptable during this window, and watchdog recovery is acceptable only for real hangs.

## Schedule

Run monthly during a known low-impact window:

- Reserve 30 minutes.
- Confirm the Core server or MQTT broker is reachable.
- Make sure someone can reach the Pi by SSH after a reboot.
- Keep the current `.env` backed up before changing packages or code.

## Pre-Checks

From the repository root on the Raspberry Pi:

```bash
hostname
date
uptime
df -h /
free -h
ip addr show
docker ps
docker compose ps
docker compose logs --tail=100 senior-pomidor-edge
ls -lt data/telemetry | head
ls -lt data/photos | head
```

Check hardware interfaces:

```bash
ls /dev/i2c-1
ls /sys/bus/w1/devices
iw dev wlan0 link
iw dev wlan0 get power_save
```

Check network reachability. Replace the host with the configured MQTT host from `.env`:

```bash
grep '^MQTT_HOST=' .env
ping -c 4 192.168.1.10
```

Back up local configuration:

```bash
mkdir -p data/backups
cp .env "data/backups/.env.$(date -u +%Y%m%dT%H%M%SZ)"
```

Emit the planned maintenance start event before stopping the container, rebooting, powering down, or servicing sensors:

```bash
python scripts/maintenance_event.py start --reason "monthly sensor service"
```

If the command exits with code `1`, the event was saved locally and will be retried the next time a maintenance event command runs. Do not treat that as a blocker when the maintenance window itself is intentional.

## OS Updates

Refresh package metadata and inspect available upgrades:

```bash
sudo apt update
apt list --upgradable
```

Apply package updates:

```bash
sudo apt full-upgrade -y
```

Reboot only when required by kernel, firmware, bootloader, Docker daemon, or hardware-interface updates:

```bash
test -f /var/run/reboot-required && cat /var/run/reboot-required
```

If a reboot is required:

```bash
sudo reboot
```

After reconnecting by SSH:

```bash
cd ~/apps/senior-pomidor-plant-v2
docker compose ps
```

Adjust the path if the repository lives somewhere else on the Pi.

## Application Updates

If this Pi tracks a Git remote, inspect and apply project updates during the same maintenance window:

```bash
git status --short
git fetch origin
git log --oneline --decorate --max-count=5 HEAD
git log --oneline --decorate --max-count=5 origin/main
```

If the working tree is clean and `origin/main` is the intended deployment version:

```bash
git pull --ff-only origin main
```

If the Pi receives code by copying files instead of Git, copy the new version before rebuilding.

Rebuild and restart the edge service:

```bash
docker compose build senior-pomidor-edge
docker compose up -d senior-pomidor-edge
```

For dependency or base-image issues, rebuild without cache during the maintenance window:

```bash
docker compose build --no-cache senior-pomidor-edge
docker compose up -d senior-pomidor-edge
```

## Post-Checks

Confirm the service is running:

```bash
docker ps
docker compose ps
docker compose logs --tail=100 senior-pomidor-edge
```

Confirm telemetry and optional photos continue:

```bash
ls -lt data/telemetry | head
ls -lt data/photos | head
```

Inspect the latest telemetry payload and verify the storage health fields under `system_health.rpi_core`:

- `filesystem_read_only` must be `false`.
- `disk_free_bytes` and `disk_free_percent` must leave enough headroom for telemetry and photos.
- `telemetry_buffer_file_count` / `telemetry_buffer_size_bytes` show queued telemetry growth.
- `photo_buffer_file_count` / `photo_buffer_size_bytes` show retained photo growth.
- `recent_io_error_count` should normally be `0`.

If `system_health.errors` contains `rpi_recent_io_errors`, the container cannot read the kernel journal. Check the host directly:

```bash
journalctl --dmesg --since "-1 hour" --no-pager | grep -Ei 'I/O error|Buffer I/O error|EXT[234]-fs error|mmc.*error|read-only file system|blk_update_request'
```

If the filesystem is read-only or I/O errors are present, stop writes before troubleshooting or replacing the MicroSD card:

```bash
findmnt -no TARGET,OPTIONS /
df -h /
sudo dmesg --level=err,crit,alert,emerg
```

Confirm hardware paths are still visible:

```bash
ls /dev/i2c-1
ls /sys/bus/w1/devices
df -h /
```

If the service was restarted or the host rebooted, follow logs for at least one telemetry interval:

```bash
docker compose logs -f senior-pomidor-edge
```

Stop following logs with `Ctrl+C`; this does not stop the container.

Emit the planned maintenance completion event after the edge service is healthy again:

```bash
python scripts/maintenance_event.py complete --reason "monthly sensor service"
```

## Rollback And Recovery

View current state:

```bash
docker compose ps
docker compose logs --tail=200 senior-pomidor-edge
systemctl status docker
journalctl -u docker --since "1 hour ago"
```

Restart only the edge container:

```bash
docker compose restart senior-pomidor-edge
```

Recreate the container from the current image:

```bash
docker compose up -d senior-pomidor-edge
```

Restore the most recent `.env` backup:

```bash
ls -lt data/backups/.env.*
cp data/backups/.env.20260611T120000Z .env
docker compose up -d senior-pomidor-edge
```

Roll back code when the deployment uses Git. Set `PREVIOUS_GOOD_COMMIT` to a known good commit from `git log`:

```bash
git log --oneline --decorate --max-count=20
PREVIOUS_GOOD_COMMIT=abc1234
git checkout "$PREVIOUS_GOOD_COMMIT"
docker compose build senior-pomidor-edge
docker compose up -d senior-pomidor-edge
```

Return to the normal branch during the next maintenance window after the issue is fixed:

```bash
git checkout main
git pull --ff-only origin main
docker compose build senior-pomidor-edge
docker compose up -d senior-pomidor-edge
```

If Docker itself is unhealthy:

```bash
sudo systemctl restart docker
docker compose up -d senior-pomidor-edge
docker compose logs --tail=100 senior-pomidor-edge
```

If hardware interfaces are missing after OS updates:

```bash
grep -E 'dtparam=i2c_arm=on|dtoverlay=w1-gpio' /boot/firmware/config.txt /boot/config.txt 2>/dev/null
sudo reboot
```

After rollback or recovery, repeat the post-checks and verify at least one new telemetry file is written.
