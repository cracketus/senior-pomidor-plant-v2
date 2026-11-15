# Season 2026 — Edge winterization and shutdown runbook

**Status: PROCEDURE ONLY — not evidence that a device has been stopped.**
Owner: Edge / Raspberry Pi / Pot Nodes. Coordinated programme record: [Senior Pomidor season closeout](https://github.com/cracketus/senior-pomidor/blob/main/docs/SEASON_2026_CLOSEOUT.md). Core procedure belongs to [the server repository](https://github.com/cracketus/senior-pomidor-server/blob/main/docs/SEASON_SHUTDOWN_2026.md).

## Safety and preconditions

- Confirm each plant/pot is no longer dependent on automated irrigation. Put pumps, lighting, heaters, fans and any future motorized actuator into a verified safe OFF state **before** touching wiring. Do not rely on network, GPIO or software state alone: isolate actuator power physically.
- Do not disconnect live 12 V/5 V wiring, wet connectors or plumbing. Unplug the external supply at its source, not only at the pod. Keep mains plugs and adapters dry.
- Inspect the actual installed paths, revisions, host units and Compose services: the documentation provides names, not proof of production state. Run commands on the **actual Raspberry Pi host**, not from a CI runner.
- Keep Core running and reachable until delivery queues have been inspected and drained, or explicitly documented as intentionally retained. Preserve the unresolved queue rather than silently deleting it.
- Capture a shutdown timestamp in Europe/Vienna plus UTC, node/pod mapping, Edge revision/image, observed plant/hardware status and operator initials in the private season manifest.

## 1. Enter a maintenance hold, while Edge is still running

The installed Edge has a host-level recovery watchdog *and* systemd/Compose restart policies. A bare `docker stop` is **not** a seasonal shutdown.

```bash
cd ~/apps/senior-pomidor-plant-v2   # adjust to actual checkout
pwd
git rev-parse HEAD
sudo systemctl status senior-pomidor-edge.service senior-pomidor-watchdog.service --no-pager
docker compose ps
docker compose exec -T senior-pomidor-edge \
  python scripts/maintenance_event.py start --reason "End of 2026 growing season"
test -f data/watchdog/maintenance.json && cat data/watchdog/maintenance.json
```

If `maintenance_event.py` returns 1, it queued (rather than delivered) its event; if it returns 2, a required step failed. Verify watchdog maintenance hold and retain queue evidence. Do not assume a delivered closeout record. Never use `maintenance_event.py complete` during storage: that removes the hold.

## 2. Check backlog and preserve local evidence

```bash
docker compose exec -T senior-pomidor-edge python scripts/telemetry_spool.py status
docker compose exec -T senior-pomidor-edge python scripts/telemetry_spool.py integrity-check
docker compose exec -T senior-pomidor-edge python scripts/telemetry_spool.py list --state pending --limit 20
docker compose exec -T senior-pomidor-edge python scripts/telemetry_spool.py list --state dead_letter --limit 20
```

Confirm HTTP deliveries against Core (MQTT is only a best-effort mirror in the current durable-spool Edge). Record pending/in-flight/dead-letter counts and any missing Core acknowledgements. Do not infer that messages arrived merely because MQTT connected. Check photo capture/upload queue and unmatched local image files using the deployed image's actual tooling; record any photo backlog. Allow bounded drain while Core is available. If the queue cannot drain, archive the entire spool and the reason; do not call the season fully reconciled.

Produce a consistent SQLite snapshot through the supported online backup API **while the container runs**:

```bash
BACKUP="data/spool-backup-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
docker compose exec -T senior-pomidor-edge python scripts/telemetry_spool.py online-backup "$BACKUP"
python3 - "$BACKUP" <<'PY'
import sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1]).resolve()
with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True) as db:
    assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
PY
sha256sum "$BACKUP"
```

Make an **encrypted off-device** copy of this verified snapshot, photos/media, relevant journald logs, hardware calibration/probe IDs, and deployed config metadata. Keep credentials/config secrets private; do not commit `.env`. The spool snapshot alone is not a full host backup. Preserve original spool and any WAL until fully backed up. Never invoke `cleanup`, `down -v`, `docker volume prune`, `rm -rf data`, or an SD-card reformat as part of shutdown.

## 3. Disable every automatic restarter and halt Edge

Stop the host supervisor first, then the Edge unit. The systemd unit's `ExecStop` stops the Compose service; `disable` prevents reboot autostart. Work in the checkout that the installed unit actually uses.

```bash
sudo systemctl disable --now senior-pomidor-watchdog.service
sudo systemctl disable --now senior-pomidor-edge.service
sudo systemctl is-enabled senior-pomidor-watchdog.service senior-pomidor-edge.service || true
sudo systemctl is-active senior-pomidor-watchdog.service senior-pomidor-edge.service || true
docker compose ps -a
```

If a container is still running, inspect why before applying a targeted `docker compose stop senior-pomidor-edge`. Do **not** stop all Docker projects on this host. If a separate deployment/watchdog timer or container exists, identify and disable it too. Only after verified halt and evidence capture:

```bash
sudo systemctl poweroff
```

Wait for shutdown completion (ACT LED/activity ceases); then disconnect power, camera and sensor cables and move the node indoors. The hardware watchdog configuration in systemd is irrelevant once the Pi is powered off, but must be reviewed before a future powered bench test.

## 4. Physical disassembly and inventory

- Disconnect **power first**. Drain reservoir, pump heads and all tubing; dry and inspect before sealed storage, preventing biological growth and freezing damage. Label each pump, polarity, supply voltage, fuse and tubing routing.
- Label both ends of sensor cables and connectors: original plant/pod, probe position/depth, channel, pinout, supply rail, calibration revision, and observed failure. Do not assume RJ45 means Ethernet.
- Inspect IP68 box, PG7 glands, seals, corrosion and condensation; remove moisture, replace damaged glands/gaskets, store dry. Keep electronics and batteries protected from freezing and condensation.
- Keep surviving probes unmodified until identity/calibration photos are captured. Clean only to manufacturer-compatible procedure; soil capacitive probes and analog ADC mappings may need fresh calibration next season.
- Preserve the Raspberry Pi 3B+ SD card or a verified disk image, boot config and software identity; inspect previously repaired SD slot before reuse. Package the Pi, PSU and camera separately to avoid connector damage.
- Inventory: **reuse**, **needs bench test**, **repair**, **replace**, **scrap**. Mark every part with the same identifier used in the experiment manifest. Do not reuse questionable waterproofing untested.

## Acceptance / evidence

- [ ] All actuator power physically OFF and irrigation drained.
- [ ] Core/backlog status captured; unresolved records/photos documented.
- [ ] Consistent spool backup validated and stored off-device.
- [ ] Watchdog **and** Edge systemd unit disabled/inactive; no active edge container.
- [ ] Pi powered down cleanly; external supply unplugged.
- [ ] All hardware labelled, inspected and stored dry; failures separated.
- [ ] Final timestamps/revision, screenshots/photos and inventory entered into season manifest.
- [ ] Core operator separately verifies **no new ingestion/writes** after the final boundary.

## Reactivation (2027, not part of shutdown)

Verify supplies, insulation, fuses, Raspberry Pi storage and watchdog configuration on a dry bench. Restore firmware/image and configuration into a staging environment; compare device IDs and calibration. Do not remove maintenance hold or enable automatic units before staging qualification and deliberate operator sign-off. On restart the existing `maintenance_event.py complete` removes the hold and emits the completion event; only use it **after** the node is verified healthy and deliberately recommissioned.
