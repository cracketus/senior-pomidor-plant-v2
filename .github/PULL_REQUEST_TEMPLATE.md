## Summary

## Validation

- [ ] `python -m pytest -q`
- [ ] `ruff format --check .`
- [ ] `ruff check .`
- [ ] `mypy src`
- [ ] Hardware validation on Raspberry Pi, if applicable

## Release and Privacy Checks

- [ ] No `.env`, credentials, private SSIDs, tokens, private telemetry, or private photos are included.
- [ ] Payload contract changes update schemas, fixtures, examples, and docs.
- [ ] Documentation uses neutral placeholders and documentation-safe IP addresses.
