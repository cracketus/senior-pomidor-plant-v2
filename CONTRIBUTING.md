# Contributing

Thanks for considering a contribution to Senior Pomidor Edge Node.

## Scope

This repository contains the Raspberry Pi edge-node runtime: sensor reads, local buffering, photo capture, telemetry formatting, and delivery contracts. Core server code, dashboards, AI/VLM processing, state estimation, and public datasets live outside this repository.

## Development Setup

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

Run the main checks before opening a pull request:

```bash
python -m pytest -q
ruff format --check .
ruff check .
mypy src
```

Hardware-only changes should also be validated on Raspberry Pi Linux when possible.

## Pull Requests

- Keep changes focused on one behavior or documentation area.
- Update tests, schemas, fixtures, or examples when changing payload contracts.
- Do not commit `.env`, local telemetry, photos, secrets, or private network identifiers.
- Use documentation-safe example IP addresses such as `192.0.2.10` and neutral SSIDs such as `example-wifi`.

## Commit Hygiene

Enable the tracked secret guard once per clone:

```bash
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
```
