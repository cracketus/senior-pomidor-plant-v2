# Edge release audit — 2026-09-24

Baseline: main `14d297e`, successful Quality and Edge RC workflows. User-authorized scope:
audit readiness, fix bounded reliability defects and open a separate PR. No hardware/deployment.

## Finding and change

The delivery worker sent an entire claimed batch even after stop was signalled. Slow HTTP requests
could multiply shutdown delay by batch size and exceed the container stop grace period.
The rate limiter also used an uninterruptible sleep.

The worker now commits the active acknowledgement, stops before another attempt, interrupts the
rate-limit wait and releases unsent claims back to pending. It returns the actual processed count.
Payloads, IDs, schema, retry policy and ACK validation are unchanged. The active request is not
forcibly cancelled; shutdown still depends on the sender timeout.

## Verification

- PASS: `python -m pytest -q`: 341 passed, 5 skipped on Python 3.12; baseline was 339 passed, 5 skipped.
- PASS: `ruff check .`, `ruff format --check .`, `mypy src`, `git diff --check`.
- Regression tests: stop mid-batch and during rate-limit wait; accepted ACK retained, unsent records
  pending with zero attempts; existing cross-batch rate-limit test retained.
- NOT_RUN locally: Docker delivery integration/smoke, target Python 3.11 CI, real Core/Edge pair,
  Raspberry Pi hardware, camera, reboot, 24h soak and rollback rehearsal.

## Decision and rollback

Ready for PR CI/review; production readiness requires final immutable pair qualification and manual
evidence. Core v0.3.1 and Edge need not have matching version numbers. Confirm Pi OS architecture.
Revert the code patch to roll back software; never erase or downgrade the spool.

The hardware runbook distinguishes local builds from immutable promotion, uses SQLite online backup,
saves the actual previous image and avoids rebuilding rollback. Previous code must support the current
spool schema. No runtime configuration or physical behavior was changed.
