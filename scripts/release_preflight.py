"""Strict, read-only production release gate for AmtHero24.

The gate combines live production smoke checks with optional verification of a recent,
encrypted, schema-compatible backup manifest. It never writes application data or prints
credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import production_smoke
from schema_recovery import expected_schema_identity


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    detail: str


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"missing required setting: {name}")
    return value


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def verify_backup_manifest(path: str, *, now: datetime | None = None, max_age_hours: int = 26) -> list[GateCheck]:
    """Validate non-secret backup and schema metadata without reading the encrypted dump."""
    if not str(path or "").strip():
        return [GateCheck("backup_manifest", False, "manifest path is required")]
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [GateCheck("backup_manifest", False, "manifest is unavailable or invalid")]
    if not isinstance(payload, dict):
        return [GateCheck("backup_manifest", False, "manifest must be a JSON object")]

    created = _parse_time(payload.get("created_at") or payload.get("generated_at"))
    current = (now or datetime.now(UTC)).astimezone(UTC)
    age_ok = created is not None and timedelta(0) <= current - created <= timedelta(hours=max(1, max_age_hours))
    artifact_hash = str(payload.get("encrypted_sha256") or payload.get("artifact_sha256") or "").strip()
    hash_ok = len(artifact_hash) == 64 and all(char in "0123456789abcdefABCDEF" for char in artifact_hash)
    dump_name = str(payload.get("artifact") or payload.get("filename") or "").strip()
    artifact_ok = bool(dump_name) and dump_name.endswith(".dump.fernet")

    expected = expected_schema_identity()
    try:
        schema_version = int(payload.get("schema_version"))
    except (TypeError, ValueError):
        schema_version = 0
    try:
        ledger_entries = int(payload.get("schema_ledger_entries"))
    except (TypeError, ValueError):
        ledger_entries = 0
    schema_checksum = str(payload.get("schema_checksum") or "").strip().lower()
    schema_contract = str(payload.get("schema_contract") or "").strip().casefold()
    schema_version_ok = schema_version == expected.version
    schema_checksum_ok = schema_checksum == expected.checksum
    schema_ledger_ok = ledger_entries == expected.ledger_entries == schema_version
    schema_contract_ok = schema_contract == "valid"

    return [
        GateCheck("backup_age", age_ok, "recent" if age_ok else "missing, future-dated, or too old"),
        GateCheck("backup_integrity_metadata", hash_ok, "present" if hash_ok else "missing or invalid"),
        GateCheck("backup_artifact_metadata", artifact_ok, "encrypted artifact recorded" if artifact_ok else "missing encrypted artifact name"),
        GateCheck("backup_schema_version", schema_version_ok, "current" if schema_version_ok else "missing or incompatible"),
        GateCheck("backup_schema_checksum", schema_checksum_ok, "compatible" if schema_checksum_ok else "missing or incompatible"),
        GateCheck("backup_schema_ledger", schema_ledger_ok, "complete" if schema_ledger_ok else "missing or inconsistent"),
        GateCheck("backup_schema_contract", schema_contract_ok, "valid" if schema_contract_ok else "missing or invalid"),
    ]


def run_gate(
    *,
    base_url: str,
    admin_token: str,
    expected_version: str,
    backup_manifest: str = "",
    require_backup: bool = False,
    timeout: float = 20.0,
) -> list[GateCheck]:
    checks = [
        GateCheck("base_url", bool(base_url.strip()), "configured" if base_url.strip() else "missing"),
        GateCheck("admin_token", bool(admin_token.strip()), "configured" if admin_token.strip() else "missing"),
        GateCheck("expected_version", bool(expected_version.strip()), expected_version.strip() or "missing"),
    ]
    if not all(item.passed for item in checks):
        return checks

    smoke = production_smoke.run_smoke(
        base_url,
        admin_token=admin_token,
        expected_version=expected_version,
        require_postgresql=True,
        require_signature=True,
        require_launch_ready=True,
        timeout=timeout,
    )
    checks.extend(GateCheck(f"smoke_{item.name}", item.passed, item.detail) for item in smoke)

    if require_backup or backup_manifest.strip():
        checks.extend(verify_backup_manifest(backup_manifest))
    else:
        checks.append(GateCheck("backup_manifest", True, "not required by this invocation"))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the strict AmtHero24 production release gate.")
    parser.add_argument("--base-url", default=os.getenv("PRODUCTION_BASE_URL", ""))
    parser.add_argument("--admin-token", default=os.getenv("ADMIN_API_TOKEN", ""))
    parser.add_argument("--expected-version", default=os.getenv("EXPECTED_APP_VERSION", ""))
    parser.add_argument("--backup-manifest", default=os.getenv("BACKUP_MANIFEST_PATH", ""))
    parser.add_argument("--require-backup", action="store_true", default=os.getenv("RELEASE_REQUIRE_RECENT_BACKUP", "false").strip().casefold() in {"1", "true", "yes", "on"})
    parser.add_argument("--timeout", type=float, default=float(os.getenv("SMOKE_TIMEOUT_SECONDS", "20")))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    checks = run_gate(
        base_url=str(args.base_url),
        admin_token=str(args.admin_token),
        expected_version=str(args.expected_version),
        backup_manifest=str(args.backup_manifest),
        require_backup=bool(args.require_backup),
        timeout=args.timeout,
    )
    passed = bool(checks) and all(item.passed for item in checks)
    if args.json:
        print(json.dumps({"passed": passed, "checks": [asdict(item) for item in checks]}, ensure_ascii=False))
    else:
        for item in checks:
            print(f"[{'PASS' if item.passed else 'FAIL'}] {item.name}: {item.detail}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
