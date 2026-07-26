"""Scan tracked AmtHero24 files for accidentally committed credentials.

The scanner is network-free and never prints a matched secret. Findings contain only a
rule name, file, line, and a short SHA-256 fingerprint so operators can correlate
repeated detections without exposing credential material.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

_MAX_FILE_BYTES = 2 * 1024 * 1024
_CONFIG_SUFFIXES = {".env", ".ini", ".cfg", ".conf", ".json", ".toml", ".yaml", ".yml"}
_SENSITIVE_NAMES = {
    "GROQ_API_KEY", "WHATSAPP_TOKEN", "META_APP_SECRET", "VERIFY_TOKEN", "ADMIN_API_TOKEN",
    "REMINDER_ENCRYPTION_KEY", "REMINDER_OLD_ENCRYPTION_KEY", "REMINDER_LEGACY_WHATSAPP_TOKEN",
    "SUPPORT_API_TOKEN", "SUPPORT_ENCRYPTION_KEY", "BACKUP_ENCRYPTION_KEY", "DATABASE_URL",
}
_PLACEHOLDER_WORDS = {
    "example", "placeholder", "dummy", "changeme", "replace-me", "set-locally", "your-",
    "isolated", "test-key", "test-token", "test-", "ci-", "recovery", "fixture", "local-only",
}
_LOCAL_DATABASE_HOSTS = {"127.0.0.1", "localhost", "postgres", "db"}
_PRIVATE_KEY_PREFIX = "-----BEGIN "
_PRIVATE_KEY_SUFFIX = "PRIVATE KEY-----"
_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(re.escape(_PRIVATE_KEY_PREFIX) + r"(?:RSA |EC |OPENSSH |DSA |PGP )?" + re.escape(_PRIVATE_KEY_SUFFIX))),
    ("groq-key", re.compile(r"\bgsk_[A-Za-z0-9_-]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("stripe-live-key", re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("meta-access-token", re.compile(r"\bEAA[A-Za-z0-9]{50,}\b")),
)
_ASSIGNMENT_PATTERN = re.compile(r"^\s*(?:export\s+)?[\"']?(?P<name>[A-Z][A-Z0-9_]+)[\"']?\s*[:=]\s*(?P<value>.+?)\s*,?\s*$")
_DATABASE_URL_PATTERN = re.compile(r"\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://[^\s'\"<>]+", re.IGNORECASE)


@dataclass(frozen=True)
class SecretFinding:
    file: str
    line: int
    rule: str
    fingerprint: str
    message: str


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _tracked_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        return [path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _clean_assignment_value(raw: str) -> str:
    value = raw.strip().rstrip(",").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def _looks_placeholder(value: str) -> bool:
    normalized = value.strip().casefold()
    if not normalized or normalized in {"null", "none", "false", "true", "0", "[]", "{}"}:
        return True
    if normalized.startswith(("${{", "${", "$", "<")):
        return True
    return any(word in normalized for word in _PLACEHOLDER_WORDS)


def _database_url_is_safe_fixture(candidate: str) -> bool:
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    password = parsed.password or ""
    username = parsed.username or ""
    return host in _LOCAL_DATABASE_HOSTS and password in {"postgres", "test", "password"} and username in {"postgres", "test"}


def _is_config_file(path: Path) -> bool:
    return path.name == ".env" or path.name.startswith(".env.") or path.suffix.casefold() in _CONFIG_SUFFIXES


def _finding(path: Path, root: Path, line: int, rule: str, value: str, message: str) -> SecretFinding:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    return SecretFinding(relative, line, rule, _fingerprint(value), message)


def scan_text(path: Path, text: str, root: Path) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    seen: set[tuple[int, str, str]] = set()
    check_assignments = _is_config_file(path)

    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in _TOKEN_PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(0)
                key = (line_number, rule, _fingerprint(value))
                if key not in seen:
                    seen.add(key)
                    findings.append(_finding(path, root, line_number, rule, value, "possible credential material is committed"))

        for match in _DATABASE_URL_PATTERN.finditer(line):
            value = match.group(0).rstrip(".,);]")
            if _database_url_is_safe_fixture(value):
                continue
            try:
                parsed = urlsplit(value)
            except ValueError:
                parsed = None
            if parsed and parsed.password:
                key = (line_number, "database-credential", _fingerprint(value))
                if key not in seen:
                    seen.add(key)
                    findings.append(_finding(path, root, line_number, "database-credential", value, "database URL contains an embedded password"))

        if not check_assignments:
            continue
        assignment = _ASSIGNMENT_PATTERN.match(line)
        if not assignment or assignment.group("name") not in _SENSITIVE_NAMES:
            continue
        value = _clean_assignment_value(assignment.group("value"))
        if assignment.group("name") == "DATABASE_URL":
            candidates = list(_DATABASE_URL_PATTERN.finditer(value))
            if not candidates or all(_database_url_is_safe_fixture(item.group(0)) for item in candidates):
                continue
        if _looks_placeholder(value):
            continue
        key = (line_number, "sensitive-assignment", _fingerprint(value))
        if key not in seen:
            seen.add(key)
            findings.append(_finding(path, root, line_number, "sensitive-assignment", value, f"{assignment.group('name')} has a committed non-placeholder value"))
    return findings


def scan_repository(root: Path, files: Iterable[Path] | None = None) -> list[SecretFinding]:
    resolved = root.resolve()
    findings: list[SecretFinding] = []
    for path in files if files is not None else _tracked_files(resolved):
        candidate = path if path.is_absolute() else resolved / path
        text = _read_text(candidate)
        if text is not None:
            findings.extend(scan_text(candidate, text, resolved))
    return sorted(findings, key=lambda item: (item.file, item.line, item.rule, item.fingerprint))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan tracked files without printing matched secrets.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    findings = scan_repository(args.root)
    report = {"status": "pass" if not findings else "fail", "finding_count": len(findings), "findings": [asdict(item) for item in findings]}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif findings:
        for item in findings:
            print(f"{item.file}:{item.line}: {item.rule}: {item.message} [{item.fingerprint}]")
    else:
        print("Repository credential scan passed.")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
