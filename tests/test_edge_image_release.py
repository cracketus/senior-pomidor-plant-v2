from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import edge_image_release

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/cracketus/senior-pomidor-edge"
DIGEST = "sha256:" + "a" * 64
COMMIT = "b" * 40
AMD64_DIGEST = "sha256:" + "c" * 64
ARM64_DIGEST = "sha256:" + "d" * 64


def _manifest(digest: str = DIGEST) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "digest": digest,
        "manifests": [
            {"digest": AMD64_DIGEST, "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": ARM64_DIGEST, "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
            {
                "digest": "sha256:" + "e" * 64,
                "platform": {"os": "unknown", "architecture": "unknown"},
            },
        ],
    }


def _config() -> dict[str, object]:
    return {"config": {"Labels": {edge_image_release.REVISION_LABEL: COMMIT}}}


def _fake_registry(monkeypatch: pytest.MonkeyPatch, release_exists: bool = False) -> list[list[str]]:
    calls: list[list[str]] = []
    release_created = release_exists

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal release_created
        calls.append(command)
        if command[3] == "create":
            release_created = True
            return subprocess.CompletedProcess(command, 0, "", "")
        reference = command[4]
        if reference.endswith(":v0.2.0") and not release_created:
            return subprocess.CompletedProcess(command, 1, "", "manifest unknown")
        if command[-1] == "{{json .Manifest}}":
            return subprocess.CompletedProcess(command, 0, json.dumps(_manifest()), "")
        return subprocess.CompletedProcess(command, 0, json.dumps(_config()), "")

    monkeypatch.setattr(edge_image_release.subprocess, "run", fake_run)
    return calls


def test_inspect_writes_rc_artifact_and_validates_both_platforms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_registry(monkeypatch)
    output = tmp_path / "rc.json"

    result = edge_image_release.main(
        [
            "inspect",
            "--image",
            IMAGE,
            "--reference",
            f"{IMAGE}:{COMMIT}",
            "--expected-commit",
            COMMIT,
            "--output",
            str(output),
        ]
    )

    assert result == 0
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "senior-pomidor.edge.release-candidate.v1"
    assert metadata["immutable_ref"] == f"{IMAGE}@{DIGEST}"
    assert metadata["platforms"] == ["linux/amd64", "linux/arm64"]


def test_inspect_rejects_revision_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_registry(monkeypatch)
    output = tmp_path / "rc.json"

    result = edge_image_release.main(
        [
            "inspect",
            "--image",
            IMAGE,
            "--reference",
            f"{IMAGE}:{COMMIT}",
            "--expected-commit",
            "f" * 40,
            "--output",
            str(output),
        ]
    )

    assert result == 2
    assert not output.exists()


def test_promote_creates_tag_without_build_and_records_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_registry(monkeypatch)
    output = tmp_path / "promotion.json"

    result = edge_image_release.main(
        [
            "promote",
            "--image",
            IMAGE,
            "--source-digest",
            DIGEST,
            "--release-tag",
            "v0.2.0",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    metadata = json.loads(output.read_text(encoding="utf-8"))
    assert metadata["result"] == "created"
    assert metadata["release_ref"] == f"{IMAGE}:v0.2.0"
    assert any(call[3] == "create" for call in calls)
    assert all("build" not in call for call in calls)


@pytest.mark.parametrize("tag", ["latest", "1.2.3", "v1", "v1.2.3+build"])
def test_promote_rejects_non_semver_release_tag(tmp_path: Path, tag: str) -> None:
    result = edge_image_release.main(
        [
            "promote",
            "--image",
            IMAGE,
            "--source-digest",
            DIGEST,
            "--release-tag",
            tag,
            "--output",
            str(tmp_path / "promotion.json"),
        ]
    )

    assert result == 2
