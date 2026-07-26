"""Privacy-safe PostgreSQL schema identity for backup and recovery verification.

Only migration versions, migration checksums, ledger counts, and aggregate schema-contract
status are read. No application rows, phone hashes, messages, documents, ciphertext, or
credentials are selected or returned.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

import database_migrations


class SchemaRecoveryError(RuntimeError):
    """Recovery compatibility failure identified by a non-sensitive code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SchemaIdentity:
    version: int
    checksum: str
    ledger_entries: int
    contract: str

    def manifest_fields(self) -> dict[str, Any]:
        return {
            "schema_version": self.version,
            "schema_checksum": self.checksum,
            "schema_ledger_entries": self.ledger_entries,
            "schema_contract": self.contract,
        }


_HEX64 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _database_url(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
    return cleaned


def expected_schema_identity() -> SchemaIdentity:
    migrations = tuple(database_migrations._MIGRATIONS)
    latest = migrations[-1]
    return SchemaIdentity(
        version=int(database_migrations.LATEST_SCHEMA_VERSION),
        checksum=str(latest.checksum),
        ledger_entries=len(migrations),
        contract="valid",
    )


def schema_identity_from_manifest(manifest: dict[str, Any]) -> SchemaIdentity:
    try:
        version = int(manifest.get("schema_version"))
        entries = int(manifest.get("schema_ledger_entries"))
    except (TypeError, ValueError):
        raise SchemaRecoveryError("backup_schema_identity_missing") from None
    checksum = str(manifest.get("schema_checksum") or "").strip().lower()
    contract = str(manifest.get("schema_contract") or "").strip().casefold()
    if version < 1 or entries < 1 or entries != version:
        raise SchemaRecoveryError("backup_schema_ledger_invalid")
    if not _HEX64.fullmatch(checksum):
        raise SchemaRecoveryError("backup_schema_checksum_invalid")
    if contract != "valid":
        raise SchemaRecoveryError("backup_schema_contract_invalid")
    return SchemaIdentity(version, checksum, entries, contract)


def require_current_manifest_schema(manifest: dict[str, Any]) -> SchemaIdentity:
    identity = schema_identity_from_manifest(manifest)
    expected = expected_schema_identity()
    if identity.version != expected.version:
        raise SchemaRecoveryError("backup_schema_version_incompatible")
    if identity.checksum != expected.checksum:
        raise SchemaRecoveryError("backup_schema_checksum_incompatible")
    if identity.ledger_entries != expected.ledger_entries:
        raise SchemaRecoveryError("backup_schema_ledger_incompatible")
    return identity


def inspect_database_schema(database_url: str, *, require_current: bool = True) -> SchemaIdentity:
    """Read only migration metadata and information_schema compatibility state."""
    url = _database_url(database_url)
    try:
        with psycopg.connect(url, row_factory=dict_row) as connection:
            ledger = connection.execute(
                "SELECT to_regclass('amthero_schema_migrations') AS relation"
            ).fetchone()
            if not ledger or ledger.get("relation") is None:
                raise SchemaRecoveryError("migration_ledger_missing")
            rows = connection.execute(
                "SELECT version, checksum FROM amthero_schema_migrations ORDER BY version"
            ).fetchall()
            if not rows:
                raise SchemaRecoveryError("migration_ledger_empty")
            versions = [int(row["version"]) for row in rows]
            if versions != list(range(1, max(versions) + 1)):
                raise SchemaRecoveryError("migration_ledger_sequence_invalid")
            checksum = str(rows[-1].get("checksum") or "").strip().lower()
            if not _HEX64.fullmatch(checksum):
                raise SchemaRecoveryError("migration_ledger_checksum_invalid")
            contract_valid, _missing = database_migrations.validate_schema_contract(connection)
            if not contract_valid:
                raise SchemaRecoveryError("database_schema_contract_invalid")
    except SchemaRecoveryError:
        raise
    except (psycopg.Error, OSError, ValueError) as exc:
        raise SchemaRecoveryError("database_schema_inspection_failed") from exc

    identity = SchemaIdentity(
        version=versions[-1],
        checksum=checksum,
        ledger_entries=len(rows),
        contract="valid",
    )
    if require_current:
        expected = expected_schema_identity()
        if identity.version != expected.version:
            raise SchemaRecoveryError("database_schema_version_incompatible")
        if identity.checksum != expected.checksum:
            raise SchemaRecoveryError("database_schema_checksum_incompatible")
        if identity.ledger_entries != expected.ledger_entries:
            raise SchemaRecoveryError("database_schema_ledger_incompatible")
    return identity


def verify_restored_schema(database_url: str, manifest: dict[str, Any]) -> SchemaIdentity:
    expected = require_current_manifest_schema(manifest)
    restored = inspect_database_schema(database_url, require_current=True)
    if asdict(restored) != asdict(expected):
        raise SchemaRecoveryError("restored_schema_identity_mismatch")
    return restored
