from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "deploy/rehearsal/edge-staging"
DIGEST = "a" * 64
IMAGE = f"ghcr.io/example/senior-pomidor-edge@sha256:{DIGEST}"
COMMIT = "b" * 40
REQUIRED_STAGING_ENV = {
    "STAGING_MQTT_HOST": "mqtt.staging.test",
    "STAGING_MQTT_USERNAME": "staging-user",
    "STAGING_MQTT_PASSWORD": "staging-password",
    "STAGING_CORE_HTTP_URL": "https://core.staging.test/telemetry",
    "STAGING_TELEMETRY_UPLOAD_TOKEN": "staging-token",
}


def test_staging_compose_is_isolated_and_persistent() -> None:
    compose = (BUNDLE / "compose.yml").read_text(encoding="utf-8")

    assert "build:" not in compose
    assert "ports:" not in compose
    assert "network_mode:" not in compose
    assert "image: ${EDGE_IMAGE:" in compose
    assert "DEVICE_ID: edge-staging-ubuntu-01" in compose
    assert 'MOCK_SENSORS: "true"' in compose
    assert "MQTT_TOPIC_PREFIX: senior-pomidor-staging" in compose
    assert "${STAGING_MQTT_HOST:?" in compose
    assert "${STAGING_MQTT_USERNAME:?" in compose
    assert "${STAGING_MQTT_PASSWORD:?" in compose
    assert "${STAGING_CORE_HTTP_URL:?" in compose
    assert "${STAGING_TELEMETRY_UPLOAD_TOKEN:?" in compose
    assert "./data:/app/data" in compose
    assert "restart: unless-stopped" in compose
    assert 'max-size: "10m"' in compose
    assert 'max-file: "5"' in compose
    assert 'CAMERA_ENABLED: "false"' in compose
    assert 'PHOTO_UPLOAD_ENABLED: "false"' in compose
    assert "senior-pomidor\n" not in compose
    assert "balcony-edge" not in compose


def test_staging_example_contains_only_staging_inputs() -> None:
    example = (BUNDLE / ".env.example").read_text(encoding="utf-8")

    assert "EDGE_IMAGE=" not in example
    assert "STAGING_MQTT_HOST=" in example
    assert "STAGING_MQTT_USERNAME=" in example
    assert "STAGING_MQTT_PASSWORD=" in example
    assert "STAGING_CORE_HTTP_URL=" in example
    assert "STAGING_TELEMETRY_UPLOAD_TOKEN=" in example
    assert "192.0.2.10" not in example


def _working_bash() -> str | None:
    if os.name == "nt":
        return None
    bash = shutil.which("bash")
    if bash is None:
        return None
    try:
        result = subprocess.run([bash, "--version"], capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bash if result.returncode == 0 else None


BASH = _working_bash()


def _copy_bundle(tmp_path: Path) -> Path:
    target = tmp_path / "edge-staging"
    shutil.copytree(BUNDLE, target)
    (target / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in REQUIRED_STAGING_ENV.items()) + "\n",
        encoding="utf-8",
    )
    return target


def _fake_docker(bin_dir: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then
  if [[ "$*" == *"org.opencontainers.image.revision"* ]]; then
    printf '%s\\n' "$FAKE_REVISION"
  else
    printf '%s\\n' 'sha256:fake-image-id'
  fi
elif [[ "${1:-}" == "inspect" ]]; then
  if [[ "$*" == *".Config.Image"* ]]; then
    printf '%s\\n' "$FAKE_IMAGE"
  else
    printf '%s\\n' 'sha256:fake-image-id'
  fi
elif [[ "$*" == *" ps -q "* ]]; then
  printf '%s\\n' 'fake-container-id'
fi
""",
        encoding="utf-8",
        newline="\n",
    )
    docker.chmod(0o755)


def _run(bundle: Path, *args: str, revision: str = COMMIT) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    bin_dir = bundle.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    _fake_docker(bin_dir)
    log = bundle.parent / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_REVISION": revision,
        "FAKE_IMAGE": IMAGE,
    }
    return subprocess.run(
        [BASH, str(bundle / "manage.sh"), *args],
        cwd=bundle,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.skipif(BASH is None, reason="a working Bash is required")
@pytest.mark.parametrize(
    "reference",
    ["ghcr.io/example/edge:latest", "example/edge@sha256:" + DIGEST, "ghcr.io/example/edge@sha256:abc"],
)
def test_deploy_rejects_tag_only_and_malformed_images(tmp_path: Path, reference: str) -> None:
    bundle = _copy_bundle(tmp_path)
    result = _run(bundle, "deploy", reference, COMMIT)

    assert result.returncode != 0
    assert "tags are forbidden" in result.stderr
    assert not (bundle / ".deployment.env").exists()


@pytest.mark.parametrize("missing", REQUIRED_STAGING_ENV)
def test_compose_rejects_missing_required_staging_variables(tmp_path: Path, missing: str) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is required")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("STAGING_MQTT_")
        and key not in {"STAGING_CORE_HTTP_URL", "STAGING_TELEMETRY_UPLOAD_TOKEN"}
    }
    env["EDGE_IMAGE"] = IMAGE
    env.update({key: value for key, value in REQUIRED_STAGING_ENV.items() if key != missing})

    result = subprocess.run(
        ["docker", "compose", "-f", str(BUNDLE / "compose.yml"), "config"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert missing in result.stderr


@pytest.mark.skipif(BASH is None, reason="a working Bash is required")
def test_deploy_rejects_revision_mismatch_before_recording_state(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    result = _run(bundle, "deploy", IMAGE, COMMIT, revision="c" * 40)

    assert result.returncode != 0
    assert "OCI revision mismatch" in result.stderr
    assert not (bundle / ".deployment.env").exists()


@pytest.mark.skipif(BASH is None, reason="a working Bash is required")
def test_deploy_pulls_digest_and_starts_without_build(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    result = _run(bundle, "deploy", IMAGE, COMMIT)
    log = (tmp_path / "docker.log").read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert f"pull {IMAGE}" in log
    assert "up -d --no-build" in log
    assert " build" not in log
    assert "configured_digest=" + IMAGE in result.stdout
    assert "running_commit=" + COMMIT in result.stdout


@pytest.mark.skipif(BASH is None, reason="a working Bash is required")
def test_reset_data_requires_exact_confirmation_and_archives(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    data = bundle / "data"
    data.mkdir()
    (data / "telemetry-spool.sqlite3").write_text("keep-me", encoding="utf-8")

    rejected = _run(bundle, "reset-data", "edge-staging-ubuntu-1")
    assert rejected.returncode != 0
    assert (data / "telemetry-spool.sqlite3").read_text(encoding="utf-8") == "keep-me"

    accepted = _run(bundle, "reset-data", "edge-staging-ubuntu-01")
    backups = list(bundle.glob("data.backup-*"))
    assert accepted.returncode == 0, accepted.stderr
    assert "stop senior-pomidor-edge-staging" in (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert len(backups) == 1
    assert (backups[0] / "telemetry-spool.sqlite3").read_text(encoding="utf-8") == "keep-me"
    assert data.is_dir()
    assert not list(data.iterdir())
