"""Pre-commit guard for staged secrets and local environment files."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

BLOCKED_BASENAMES = {
    ".env",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.toml",
    "service-account.json",
}
BLOCKED_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
SAFE_ENV_BASENAMES = {".env.example"}
PLACEHOLDER_VALUES = {
    "",
    "<redacted>",
    "<secret>",
    "change-me",
    "changeme",
    "dummy",
    "example",
    "fake",
    "none",
    "null",
    "optional-bearer-token",
    "password",
    "placeholder",
    "secret",
    "test",
    "token",
    "your-token",
}

SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b[A-Z0-9_-]*(
        api[_-]?key |
        authorization |
        client[_-]?secret |
        password |
        passwd |
        private[_-]?key |
        secret |
        token
    )[A-Z0-9_-]*\b
    \s*[:=]\s*
    (?P<value>["']?[^"'\s#]+["']?)
    """
)
TOKEN_RE = re.compile(
    r"""(?x)
    \b(
        gh[pousr]_[A-Za-z0-9_]{36,} |
        github_pat_[A-Za-z0-9_]{30,} |
        xox[baprs]-[A-Za-z0-9-]{20,} |
        sk-[A-Za-z0-9]{20,}
    )\b
    """
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")


@dataclass(frozen=True)
class Finding:
    path: str
    message: str


def main() -> int:
    findings = scan_staged_changes()
    gitleaks_result = run_gitleaks_if_available()

    if findings:
        print("Pre-commit secret check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding.path}: {finding.message}", file=sys.stderr)
        print("\nRemove the secret, unstage the file, or replace real values with placeholders.", file=sys.stderr)
        return 1

    if gitleaks_result != 0:
        return gitleaks_result

    return 0


def scan_staged_changes() -> list[Finding]:
    findings: list[Finding] = []
    for path in staged_paths():
        path_findings = scan_path(path)
        if path_findings:
            findings.extend(path_findings)
            continue
        content = staged_text(path)
        if content is None:
            continue
        findings.extend(scan_text(path, content))
    return findings


def staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or "Could not read staged files.", file=sys.stderr)
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def scan_path(path: str) -> list[Finding]:
    normalized = path.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1].lower()
    suffix = "." + basename.rsplit(".", 1)[-1] if "." in basename else ""

    if basename in SAFE_ENV_BASENAMES:
        return []
    if basename in BLOCKED_BASENAMES:
        return [Finding(path, "sensitive local file name must not be committed")]
    if basename.startswith(".env."):
        return [Finding(path, "local environment files must not be committed")]
    if suffix in BLOCKED_SUFFIXES:
        return [Finding(path, f"files ending in {suffix} may contain private credentials")]
    if normalized.lower().endswith("/secrets.toml"):
        return [Finding(path, "secrets.toml must not be committed")]
    return []


def staged_text(path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f":{path}"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or b"\x00" in result.stdout:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_text(path: str, content: str) -> list[Finding]:
    findings: list[Finding] = []
    if PRIVATE_KEY_RE.search(content):
        findings.append(Finding(path, "private key material detected"))
    if TOKEN_RE.search(content):
        findings.append(Finding(path, "well-known token format detected"))

    for line_number, line in enumerate(content.splitlines(), start=1):
        match = SECRET_ASSIGNMENT_RE.search(line)
        if not match:
            continue
        value = match.group("value").strip("\"'")
        if is_placeholder_value(value):
            continue
        findings.append(Finding(path, f"possible credential assignment on line {line_number}"))
    return findings


def is_placeholder_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in PLACEHOLDER_VALUES:
        return True
    if normalized.startswith(("<", "${", "example-", "your-", "re.compile")):
        return True
    if normalized.endswith(("-example", "_example")):
        return True
    if len(value) < 8:
        return True
    return entropy(value) < 3.0


def entropy(value: str) -> float:
    if not value:
        return 0.0
    return -sum((value.count(char) / len(value)) * math.log2(value.count(char) / len(value)) for char in set(value))


def run_gitleaks_if_available() -> int:
    if shutil.which("gitleaks") is None:
        return 0
    result = subprocess.run(
        ["gitleaks", "protect", "--staged", "--redact", "--config", ".gitleaks.toml"],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
