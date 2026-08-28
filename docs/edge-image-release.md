# Edge image release and promotion

The repository publishes one release-candidate image after the `Quality` workflow succeeds for a push to `main`. Pull requests, fork workflows, failed quality runs, and manually edited tags do not publish images.

The package is `ghcr.io/cracketus/senior-pomidor-edge`. The build produces `linux/amd64` and `linux/arm64` from one tested commit with `INSTALL_HARDWARE_DEPS=true`. The only CI tag is the full 40-character commit SHA. There is no `latest` tag.

## Release-candidate artifact

Open the successful `Edge release candidate` workflow run associated with the merge commit and download the `edge-release-candidate-<SHA>` artifact. Its `edge-release-candidate.json` is the deployment record. It contains the registry digest, immutable reference, SHA tag, commit SHA, required platforms, and workflow URL.

Before deploying to staging, confirm:

```text
schema_version = senior-pomidor.edge.release-candidate.v1
immutable_ref  = ghcr.io/cracketus/senior-pomidor-edge@sha256:<64 hex>
commit_sha     = <40 hex>
platforms      = ["linux/amd64", "linux/arm64"]
```

The image config for both runnable platform manifests must contain `org.opencontainers.image.revision` equal to `commit_sha`. Pass the exact `immutable_ref` and SHA to [the Ubuntu staging runbook](edge-staging-runbook.md); staging validates the label again before starting the container.

For a private package, authenticate with a read-only package token and never paste it into a workflow input or shell command argument:

```bash
read -rsp 'GHCR token: ' GHCR_TOKEN; echo
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u GITHUB_USER --password-stdin
unset GHCR_TOKEN
```

## Promotion to a release tag

Use the **Promote edge image** workflow's `Run workflow` form. Enter the digest from the release-candidate artifact and a new SemVer tag such as `v0.2.0` or `v0.2.0-rc.1`.

The workflow verifies the source digest, both platforms, matching revision labels, and the commit-SHA tag before running only:

```bash
docker buildx imagetools create \
  --tag ghcr.io/cracketus/senior-pomidor-edge:v0.2.0 \
  ghcr.io/cracketus/senior-pomidor-edge@sha256:<validated-digest>
```

No Dockerfile build, dependency install, or platform rebuild occurs during promotion. A release tag that already points to the same digest is an idempotent success; a tag pointing elsewhere is rejected. The workflow uploads `edge-image-promotion.json` as immutable evidence.

Production and hardware deployments should continue to use the digest from the artifact, not a mutable tag. A rollback selects a previously recorded digest and does not rebuild the image.
