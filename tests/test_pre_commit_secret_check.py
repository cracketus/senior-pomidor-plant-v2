from scripts import pre_commit_secret_check as check


def test_blocks_local_env_files() -> None:
    findings = check.scan_path(".env.production")

    assert findings
    assert "environment" in findings[0].message


def test_allows_env_example() -> None:
    assert check.scan_path(".env.example") == []


def test_blocks_private_key_suffix() -> None:
    findings = check.scan_path("deploy/prod.pem")

    assert findings
    assert ".pem" in findings[0].message


def test_detects_real_looking_secret_assignment() -> None:
    content = "API_TOK" + "EN='abcd1234EFGH5678ijkl9012'\n"

    findings = check.scan_text("settings.py", content)

    assert findings
    assert "line 1" in findings[0].message


def test_allows_placeholder_secret_assignment() -> None:
    content = "PHOTO_UPLOAD_TOK" + "EN=optional-bearer-token\n"

    findings = check.scan_text("README.md", content)

    assert findings == []


def test_detects_private_key_material() -> None:
    content = "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nabc\n"

    findings = check.scan_text("key.txt", content)

    assert findings
    assert "private key" in findings[0].message


def test_allows_regex_declarations() -> None:
    findings = check.scan_text("guard.py", "TOKEN_RE = re.compile(r'example')\n")

    assert findings == []
