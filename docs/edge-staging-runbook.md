# Ubuntu software-staging edge runbook

This bundle runs one permanent mock-sensor edge on an operator-managed Ubuntu host. It is isolated from production by its fixed device ID (`edge-staging-ubuntu-01`), MQTT namespace (`senior-pomidor-staging/#`), and staging-only Core URL and credentials. Camera capture and photo upload remain disabled until #102.

## Provision staging destinations

Before installing the edge, provision a separate Core ingestion URL and token. The URL may use HTTP only when it is an isolated staging endpoint; prefer HTTPS. Never copy a production URL or token into this bundle.

Provision a dedicated MQTT user even if staging shares the physical broker. Its broker ACL must allow only the operations the edge needs under `senior-pomidor-staging/#` and must deny production namespaces. For example, grant publish access to `senior-pomidor-staging/#`; add subscribe access only if the edge contract requires it. Test the ACL with the staging credentials before deployment and verify that publishing to `senior-pomidor/#` is denied.

## Install the bundle

Docker Engine and the Compose plugin must already be installed, and the Docker service must be enabled at boot. Copy the tracked bundle to its fixed location without copying local state:

```bash
sudo install -d -m 0750 /srv/rehearsal/edge-staging
sudo cp deploy/rehearsal/edge-staging/compose.yml deploy/rehearsal/edge-staging/manage.sh \
  deploy/rehearsal/edge-staging/.env.example /srv/rehearsal/edge-staging/
sudo chown -R "$USER":"$(id -gn)" /srv/rehearsal/edge-staging
sudo chmod 0750 /srv/rehearsal/edge-staging/manage.sh
cd /srv/rehearsal/edge-staging
sudo cp .env.example .env
sudo chmod 0600 .env
sudo editor .env
```

The operator account must be allowed to use Docker. Replace every placeholder with staging-only values. Do not add `EDGE_IMAGE` to `.env`; `manage.sh` records that value only after verification. Keep `.env`, `.deployment.env`, `.previous-deployment.env`, `data/`, and `data.backup-*` out of source control. Protect the state and data directories with the host backup policy, treating `.env` as a secret.

For a private GHCR package, authenticate the operator account with a read-only package token without putting it on the command line:

```bash
read -rsp 'GHCR token: ' GHCR_TOKEN; echo
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u GITHUB_USER --password-stdin
unset GHCR_TOKEN
```

## Deploy and update

Use the multi-architecture `linux/amd64` artifact produced by #99. The package is `ghcr.io/cracketus/senior-pomidor-edge`; obtain its immutable digest reference and full 40-character source commit SHA from the `edge-release-candidate.json` artifact. The image must carry the same SHA in `org.opencontainers.image.revision`; deployment fails closed otherwise.

```bash
cd /srv/rehearsal/edge-staging
./manage.sh deploy ghcr.io/cracketus/senior-pomidor-edge@sha256:64_HEX_DIGEST 40_HEX_COMMIT
./manage.sh version
```

Tags and locally built images are rejected. Deploy pulls the digest directly, never rebuilds it, validates the revision label, records the current and previous selections, and runs `docker compose up -d --no-build`.

The `unless-stopped` policy brings the container back after a process crash, Docker daemon restart, or host reboot. An intentional `./manage.sh stop` stays stopped until `./manage.sh start`.

Verify restart and reboot behavior:

```bash
./manage.sh restart
./manage.sh version
sudo reboot
# after reconnecting
cd /srv/rehearsal/edge-staging
./manage.sh version
```

## Inspect and operate

```bash
./manage.sh logs --tail 200
./manage.sh logs -f
./manage.sh spool-status
./manage.sh stop
./manage.sh start
```

To prove persistence, create or observe a pending spool record while the staging Core is unavailable, record `spool-status`, restart the container, and confirm that the same pending record remains. The bind mount preserves the SQLite spool (including WAL/SHM), legacy telemetry/events, watchdog state, and photo queue under `data/`.

After every deployment, query the staging MQTT topic and staging Core for device `edge-staging-ubuntu-01`. Then query production telemetry storage and dashboards for that device ID and the `senior-pomidor-staging` prefix; both production queries must return no records. Treat any result as an isolation incident: stop the edge and rotate the staging credentials before investigating routing and ACLs.

## Roll back and recover data

Rollback re-pulls and verifies the previously recorded digest and changes no persistent data:

```bash
./manage.sh rollback
./manage.sh version
./manage.sh spool-status
```

To reset staging state, supply the exact fixed device ID. The command stops the container and moves the whole data directory to a timestamped backup; it does not delete data:

```bash
./manage.sh reset-data edge-staging-ubuntu-01
./manage.sh start
```

The command prints the backup path, such as `data.backup-20260828T120000Z`. To recover it, stop the container, move the new `data` directory aside, move the chosen backup back to `data`, and start the container. Preserve the SQLite database with its `-wal` and `-shm` siblings.
