# Edge release runbook

This is the legacy source-built hardware procedure, not immutable RC qualification. For promotion,
use [edge-image-release.md](edge-image-release.md). Check the actual Pi OS architecture: current RC
images support amd64/arm64, not 32-bit arm/v7. A Pi model alone does not establish OS architecture.

Release from a Windows laptop to a Raspberry Pi node at `~/apps/senior-pomidor-plant-v2`.

## Windows: merge, tag, release

```powershell
Set-Location E:\MyProjects\senior-pomidor-plant-v2
$VERSION = "v0.2.0"
git switch main
git pull --ff-only origin main
gh pr checks 140
$COMMIT_SHA = (git rev-parse HEAD).Trim()
if ($COMMIT_SHA.Length -ne 40) { throw "Unexpected commit SHA" }
git tag -a $VERSION -m "Release $VERSION"
git push origin $VERSION
gh release create $VERSION --target main --generate-notes --title "Senior Pomidor $VERSION"
```

The Git tag does not itself publish an image. Check whether the release-candidate workflow exists:

```powershell
gh workflow list
gh run list --workflow "Edge release candidate" --branch main --limit 5
```

If it is unavailable, stop immutable promotion. A separately authorized local hardware build is a
different candidate and needs its own compatibility and rollback evidence.

## Optional GHCR promotion

When the workflow is available, download its artifact and promote the validated digest:

```powershell
$RUN_ID = "<successful-workflow-run-id>"
gh run watch $RUN_ID --exit-status
gh run download $RUN_ID --name "edge-release-candidate-$COMMIT_SHA" --dir release-artifact
$release = Get-Content .\release-artifact\edge-release-candidate.json | ConvertFrom-Json
$SOURCE_DIGEST = $release.digest
if ($release.commit_sha -ne $COMMIT_SHA) { throw "Artifact commit mismatch" }
gh workflow run "Promote edge image" -f source_digest=$SOURCE_DIGEST -f release_tag=$VERSION
```

Use the digest on the node only if Compose explicitly uses `image:`. The current hardware Compose uses local `build:`.

## Edge: preflight and maintenance

```bash
ssh <user>@<edge-host>
cd ~/apps/senior-pomidor-plant-v2
git status --short
test -s .env
sudo cp .env ".env.backup-$(date +%Y%m%d-%H%M%S)"
PREVIOUS_COMMIT="$(git rev-parse HEAD)"
PREVIOUS_IMAGE_ID="$(docker inspect --format '{{.Image}}' senior-pomidor-edge)"
PREVIOUS_IMAGE_REF="$(docker inspect --format '{{.Config.Image}}' senior-pomidor-edge)"
ROLLBACK_IMAGE="senior-pomidor-edge:rollback-${PREVIOUS_COMMIT}"
docker image tag "$PREVIOUS_IMAGE_ID" "$ROLLBACK_IMAGE"
```

Record these identities privately before a build can replace the local image tag. Do not archive a
live SQLite DB/WAL with `tar` and treat it as a consistent snapshot. Use the online backup API:

```bash
SPOOL_BACKUP="data/spool-backup-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
docker compose exec -T senior-pomidor-edge python scripts/telemetry_spool.py online-backup "$SPOOL_BACKUP"
python3 - "$SPOOL_BACKUP" <<'PY'
import sqlite3
import sys
from pathlib import Path
with sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True) as db:
    assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
PY
```

Copy the checked snapshot off-device to a private backup location. This spool snapshot is not a
complete host backup. Preserve configuration privately; never restore over the active spool or
discard pending records.

`.env` is the single source for Compose, both systemd units, and watchdog. Keep `SERVICE_NAME` unset for Docker and set `WATCHDOG_SERVICE_NAME=senior-pomidor-edge.service`.

Host Python may lack `python-dotenv`; create the maintenance hold inside the container:

```bash
docker compose exec senior-pomidor-edge \
  python scripts/maintenance_event.py start --reason "Deploy v0.2.0"
cat data/watchdog/maintenance.json
```

If the command succeeds but the file is absent, the host watchdog is not configured. Install it before continuing.

## Edge: watchdog installation

Do not invoke the setup script as `/bin/sh`; it requires Bash. Raspberry Pi OS Trixie replaces `libraspberrypi-bin` with `raspi-utils-core` and `libgpiod2` with `libgpiod3`:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git i2c-tools fswebcam v4l-utils wireless-tools raspi-utils-core libgpiod3
command -v vcgencmd
vcgencmd get_throttled
```

If the package list in the release has been fixed, run:

```bash
sudo bash ./scripts/setup_raspberry_pi.sh --hardware --skip-start
```

Otherwise install units with the actual checkout path and create the intent marker:

```bash
REPO_DIR="$(pwd)"; TARGET_USER="$(id -un)"; TARGET_GROUP="$(id -gn)"
sudo install -d -o "$TARGET_USER" -g "$TARGET_GROUP" -m 0750 "$REPO_DIR/data/watchdog"
sudo sed "s|/opt/senior-pomidor-plant-v2|$REPO_DIR|g" deploy/systemd/senior-pomidor-edge.service | sudo tee /etc/systemd/system/senior-pomidor-edge.service >/dev/null
sudo sed "s|/opt/senior-pomidor-plant-v2|$REPO_DIR|g" deploy/systemd/senior-pomidor-watchdog.service | sudo tee /etc/systemd/system/senior-pomidor-watchdog.service >/dev/null
sudo install -d /etc/systemd/system.conf.d
printf '%s\n' '[Manager]' 'RuntimeWatchdogSec=30s' 'RebootWatchdogSec=10min' | sudo tee /etc/systemd/system.conf.d/senior-pomidor-hardware-watchdog.conf >/dev/null
sudo systemctl daemon-reexec; sudo systemctl daemon-reload
sudo install -o "$TARGET_USER" -g "$TARGET_GROUP" -m 0644 /dev/null "$REPO_DIR/data/watchdog/installed"
sudo systemctl enable senior-pomidor-edge.service senior-pomidor-watchdog.service
```

Repeat the container maintenance command and verify `data/watchdog/maintenance.json` exists.

## Edge: checkout and deploy

If local edits block checkout, preserve them:

```bash
git diff -- scripts/setup_raspberry_pi.sh > ~/setup_raspberry_pi.local.patch
git stash push -m "local setup changes" -- scripts/setup_raspberry_pi.sh
```

```bash
git fetch origin --tags
git checkout --detach v0.2.0
git describe --tags --exact-match HEAD
test -s .env
```

Reinstall units using the actual path, then build and restart:

```bash
REPO_DIR="$(pwd)"
sudo sed "s|/opt/senior-pomidor-plant-v2|$REPO_DIR|g" deploy/systemd/senior-pomidor-edge.service | sudo tee /etc/systemd/system/senior-pomidor-edge.service >/dev/null
sudo sed "s|/opt/senior-pomidor-plant-v2|$REPO_DIR|g" deploy/systemd/senior-pomidor-watchdog.service | sudo tee /etc/systemd/system/senior-pomidor-watchdog.service >/dev/null
sudo systemctl daemon-reload
docker compose build --pull senior-pomidor-edge
sudo systemctl restart senior-pomidor-edge.service
sudo systemctl restart senior-pomidor-watchdog.service
```

## Validation and completion

```bash
sudo systemctl --no-pager --full status senior-pomidor-edge.service senior-pomidor-watchdog.service
docker compose ps
docker compose logs --since 10m senior-pomidor-edge
cat data/watchdog/status.json
cat data/watchdog/heartbeat.json
```

Expect both services active, container `Up`, regular MQTT telemetry, and `watchdog_state=healthy` with a fresh heartbeat. Camera errors are separate; check `/dev/video0` if needed.

Complete maintenance only after validation:

```bash
docker compose exec senior-pomidor-edge python scripts/maintenance_event.py complete --reason "Deploy v0.2.0 completed"
test ! -e data/watchdog/maintenance.json && echo "maintenance hold cleared"
```

Canonical Docker telemetry should have `service_manager=none`, `process_running=true`, no `systemd_*`, healthy watchdog, and aggregate `OK`. If Grafana shows `UNKNOWN`, inspect the exact Core/Edge versions, discriminator and freshness. The Core evaluator fix already exists; do not infer a missing server patch from `UNKNOWN` alone.

## Rollback

```bash
cd ~/apps/senior-pomidor-plant-v2
docker compose exec senior-pomidor-edge python scripts/maintenance_event.py start --reason "Rollback"
git checkout --detach "$PREVIOUS_COMMIT"
test "$(docker image inspect --format '{{.Id}}' "$ROLLBACK_IMAGE")" = "$PREVIOUS_IMAGE_ID"
docker image tag "$ROLLBACK_IMAGE" "$PREVIOUS_IMAGE_REF"
sudo systemctl restart senior-pomidor-edge.service
sudo systemctl restart senior-pomidor-watchdog.service
docker compose ps
docker compose exec -T senior-pomidor-edge python scripts/telemetry_spool.py status
```

Verify fresh acquisition, health and queue progress as above before clearing the hold:

```bash
docker compose exec senior-pomidor-edge python scripts/maintenance_event.py complete --reason "Rollback completed"
```

Do not rebuild rollback. Confirm the saved image reference matches the restored Compose configuration;
otherwise stop and prepare a reviewed override. Keep the spool/data intact and verify in rehearsal that
the previous code supports the current spool schema. Physical sensors, camera and reboot need manual evidence.
