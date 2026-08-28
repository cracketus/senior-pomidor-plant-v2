#!/usr/bin/env python3
"""Validate and promote the Senior Pomidor multi-platform edge image."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

IMAGE_RE = re.compile(r"^ghcr\.io/[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_TAG_RE = re.compile(
    r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
REVISION_LABEL = "org.opencontainers.image.revision"
EXPECTED_PLATFORMS = {"linux/amd64", "linux/arm64"}


class ReleaseError(ValueError):
    """Raised when a registry image cannot satisfy the release contract."""


def _validate_image(image: str) -> None:
    if not IMAGE_RE.fullmatch(image):
        raise ReleaseError("image must be a bare ghcr.io/<repository> reference")


def _validate_digest(digest: str) -> None:
    if not DIGEST_RE.fullmatch(digest):
        raise ReleaseError("digest must match sha256:<64 lowercase hexadecimal characters>")


def _validate_commit(commit: str) -> None:
    if not COMMIT_RE.fullmatch(commit):
        raise ReleaseError("commit SHA must contain exactly 40 lowercase hexadecimal characters")


def _validate_release_tag(tag: str) -> None:
    if not RELEASE_TAG_RE.fullmatch(tag):
        raise ReleaseError("release tag must match vMAJOR.MINOR.PATCH[-prerelease]")


def _docker_json(reference: str, template: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference, "--format", template],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"registry inspect failed for {reference}: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"registry inspect returned invalid JSON for {reference}") from exc
    if not isinstance(payload, dict):
        raise ReleaseError(f"registry inspect returned a non-object for {reference}")
    return payload


def _inspect_manifest(image: str, reference: str) -> tuple[str, dict[str, str]]:
    manifest = _docker_json(reference, "{{json .Manifest}}")
    digest = manifest.get("digest")
    if not isinstance(digest, str):
        raise ReleaseError(f"manifest for {reference} has no digest")
    _validate_digest(digest)

    revisions: dict[str, str] = {}
    seen_platforms: set[str] = set()
    entries = manifest.get("manifests")
    if not isinstance(entries, list):
        raise ReleaseError(f"{reference} is not a multi-platform image index")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        platform = entry.get("platform")
        if not isinstance(platform, dict) or platform.get("os") != "linux":
            continue
        architecture = platform.get("architecture")
        platform_name = f"linux/{architecture}" if isinstance(architecture, str) else ""
        if platform_name not in EXPECTED_PLATFORMS:
            continue
        child_digest = entry.get("digest")
        if not isinstance(child_digest, str) or not DIGEST_RE.fullmatch(child_digest):
            raise ReleaseError(f"{platform_name} manifest has an invalid child digest")
        if platform_name in seen_platforms:
            raise ReleaseError(f"duplicate {platform_name} manifest")
        seen_platforms.add(platform_name)

        image_config = _docker_json(f"{image}@{child_digest}", "{{json .Image}}")
        config = image_config.get("config") or image_config.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        revision = labels.get(REVISION_LABEL) if isinstance(labels, dict) else None
        if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
            raise ReleaseError(f"{platform_name} image has no valid {REVISION_LABEL} label")
        revisions[platform_name] = revision

    if seen_platforms != EXPECTED_PLATFORMS:
        missing = ", ".join(sorted(EXPECTED_PLATFORMS - seen_platforms))
        raise ReleaseError(f"manifest is missing required platform(s): {missing}")
    if len(set(revisions.values())) != 1:
        raise ReleaseError("platform images do not carry the same revision label")
    return digest, revisions


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _workflow_url(args: argparse.Namespace) -> str:
    return args.workflow_url or ""


def inspect_command(args: argparse.Namespace) -> int:
    _validate_image(args.image)
    _validate_commit(args.expected_commit)
    digest, revisions = _inspect_manifest(args.image, args.reference)
    if args.expected_digest:
        _validate_digest(args.expected_digest)
        if digest != args.expected_digest:
            raise ReleaseError(f"registry digest mismatch: expected {args.expected_digest}, got {digest}")
    commit = next(iter(revisions.values()))
    if commit != args.expected_commit:
        raise ReleaseError(f"revision mismatch: expected {args.expected_commit}, got {commit}")
    payload = {
        "schema_version": "senior-pomidor.edge.release-candidate.v1",
        "image": args.image,
        "digest": digest,
        "immutable_ref": f"{args.image}@{digest}",
        "sha_tag": args.reference,
        "commit_sha": commit,
        "platforms": sorted(revisions),
        "workflow_run_url": _workflow_url(args),
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    _write_json(Path(args.output), payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _create_tag(source_ref: str, release_ref: str) -> None:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "create", "--tag", release_ref, source_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"release tag creation failed: {detail}")


def promote_command(args: argparse.Namespace) -> int:
    _validate_image(args.image)
    _validate_digest(args.source_digest)
    _validate_release_tag(args.release_tag)
    source_ref = f"{args.image}@{args.source_digest}"
    release_ref = f"{args.image}:{args.release_tag}"
    source_digest, revisions = _inspect_manifest(args.image, source_ref)
    commit = next(iter(revisions.values()))
    sha_ref = f"{args.image}:{commit}"

    try:
        sha_digest, _ = _inspect_manifest(args.image, sha_ref)
    except ReleaseError as exc:
        raise ReleaseError(f"source digest is not addressable by its commit SHA tag: {exc}") from exc
    if sha_digest != source_digest:
        raise ReleaseError(f"commit SHA tag points to {sha_digest}, not source digest {source_digest}")

    result = "created"
    try:
        release_digest, _ = _inspect_manifest(args.image, release_ref)
    except ReleaseError as exc:
        detail = str(exc).lower()
        if (
            "not found" not in detail
            and "manifest unknown" not in detail
            and "no such manifest" not in detail
            and "404" not in detail
        ):
            raise
        _create_tag(source_ref, release_ref)
        release_digest, _ = _inspect_manifest(args.image, release_ref)
    else:
        result = "already_present"

    if release_digest != source_digest:
        raise ReleaseError(f"release tag points to {release_digest}, not source digest {source_digest}")
    payload = {
        "schema_version": "senior-pomidor.edge.image-promotion.v1",
        "image": args.image,
        "source_digest": source_digest,
        "source_ref": source_ref,
        "release_tag": args.release_tag,
        "release_ref": release_ref,
        "commit_sha": commit,
        "result": result,
        "workflow_run_url": _workflow_url(args),
        "actor": args.actor or "",
        "promoted_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    _write_json(Path(args.output), payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="validate an RC image index")
    inspect_parser.add_argument("--image", required=True)
    inspect_parser.add_argument("--reference", required=True)
    inspect_parser.add_argument("--expected-commit", required=True)
    inspect_parser.add_argument("--expected-digest")
    inspect_parser.add_argument("--output", required=True)
    inspect_parser.add_argument("--workflow-url")
    inspect_parser.set_defaults(handler=inspect_command)

    promote_parser = subparsers.add_parser("promote", help="promote a digest to a release tag")
    promote_parser.add_argument("--image", required=True)
    promote_parser.add_argument("--source-digest", required=True)
    promote_parser.add_argument("--release-tag", required=True)
    promote_parser.add_argument("--output", required=True)
    promote_parser.add_argument("--workflow-url")
    promote_parser.add_argument("--actor")
    promote_parser.set_defaults(handler=promote_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
