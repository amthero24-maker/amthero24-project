"""Unit tests for privacy-safe database migration policy."""
from __future__ import annotations

from types import SimpleNamespace

import database_migrations as migrations


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _ContractConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, query, params=None):
        return _Rows(self.rows)


def test_lock_timeout_setting_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("SCHEMA_MIGRATION_LOCK_TIMEOUT_SECONDS", "0")
    assert migrations.migration_lock_timeout_seconds() == 1.0

    monkeypatch.setenv("SCHEMA_MIGRATION_LOCK_TIMEOUT_SECONDS", "999")
    assert migrations.migration_lock_timeout_seconds() == 120.0

    monkeypatch.setenv("SCHEMA_MIGRATION_LOCK_TIMEOUT_SECONDS", "invalid")
    assert migrations.migration_lock_timeout_seconds() == 30.0


def test_contract_checksum_is_deterministic_and_non_secret() -> None:
    first = migrations._contract_checksum(1, "stable", {"b": ("two",), "a": ("one",)})
    second = migrations._contract_checksum(1, "stable", {"a": ("one",), "b": ("two",)})

    assert first == second
    assert len(first) == 64
    assert first.isalnum()


def test_schema_contract_reports_only_missing_schema_identifiers() -> None:
    rows = []
    for table, columns in migrations._EXPECTED_SCHEMA.items():
        for column in columns or ("placeholder",):
            rows.append({"table_name": table, "column_name": column})
    rows = [
        row
        for row in rows
        if not (row["table_name"] == "inbound_work_queue" and row["column_name"] == "lease_owner")
    ]

    valid, missing = migrations.validate_schema_contract(_ContractConnection(rows))

    assert valid is False
    assert missing == ("column:inbound_work_queue.lease_owner",)
    encoded = " ".join(missing).casefold()
    assert "phone_hash_value" not in encoded
    assert "message text" not in encoded
    assert "ciphertext" not in encoded


def test_migration_readiness_is_safe_and_backend_aware() -> None:
    json_store = SimpleNamespace(backend_name="json")
    assert migrations.migration_readiness(json_store) == ("not-applicable", 0)

    postgres_without_report = SimpleNamespace(backend_name="postgresql")
    assert migrations.migration_readiness(postgres_without_report) == ("unverified", 0)

    report = migrations.MigrationReport(
        status="current",
        current_version=1,
        required_version=1,
        applied_versions=(1,),
        components=("hero_memory",),
        schema_checksum="a" * 64,
    )
    postgres = SimpleNamespace(backend_name="postgresql", schema_migration_report=report)
    assert migrations.migration_readiness(postgres) == ("current", 1)


def test_migration_report_contains_no_user_or_request_fields() -> None:
    fields = set(migrations.MigrationReport.__dataclass_fields__)
    assert fields == {
        "status",
        "current_version",
        "required_version",
        "applied_versions",
        "components",
        "schema_checksum",
    }
    forbidden = {"phone", "message", "document", "sender", "ciphertext", "token", "payload"}
    assert not fields & forbidden
