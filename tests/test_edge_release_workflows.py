from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_candidate_workflow_is_gated_and_publishes_contract() -> None:
    workflow = (ROOT / ".github/workflows/edge-release-candidate.yml").read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "packages: write" in workflow
    assert "platforms: linux/amd64,linux/arm64" in workflow
    assert "INSTALL_HARDWARE_DEPS=true" in workflow
    assert "org.opencontainers.image.revision=${{ env.SOURCE_SHA }}" in workflow
    assert "edge-release-candidate.json" in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_promotion_workflow_has_manual_inputs_and_no_build() -> None:
    workflow = (ROOT / ".github/workflows/edge-image-promotion.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "source_digest:" in workflow
    assert "release_tag:" in workflow
    assert "docker/build-push-action" not in workflow
    assert "scripts/edge_image_release.py promote" in workflow
    assert "edge-image-promotion.json" in workflow


def test_quality_workflow_runs_actionlint() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "rhysd/actionlint:1.7.12" in workflow


def test_dockerignore_keeps_only_release_build_inputs() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".git" in dockerignore
    assert ".env" in dockerignore
    assert "data" in dockerignore
    assert "tests" in dockerignore
    assert "deploy" in dockerignore
    assert "src" not in dockerignore
    assert "scripts" not in dockerignore
