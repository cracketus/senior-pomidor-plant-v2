# Security Policy

## Supported Versions

Security fixes are accepted for the latest tagged release and the default branch.

## Reporting a Vulnerability

Please do not open a public issue for suspected vulnerabilities, leaked credentials, or private deployment details. Report privately through GitHub Security Advisories for this repository, or contact the maintainer directly if advisories are unavailable.

Include:

- Affected files, configuration, or deployment mode.
- Reproduction steps.
- Potential impact.
- Whether any private network names, tokens, telemetry, photos, or credentials may be exposed.

## Sensitive Data

Do not commit:

- `.env` files or production configuration.
- MQTT, HTTP, Wi-Fi, SSH, or dashboard credentials.
- Private keys or tokens.
- Raw plant photos or telemetry that should not be public.
- Real SSIDs or private deployment identifiers unless deliberately approved for release.

Use `.env.example`, `examples/`, and documentation-safe placeholders for public material.
