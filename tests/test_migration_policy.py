"""Regression tests for online-safe migration policy and runtime enforcement."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import database_migrations
from migration_policy import assess_online_sql, validate_migration_source


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE IF NOT EXISTS example_items (item_id TEXT PRIMARY KEY)",
        "CREATE INDEX IF NOT EXISTS example_items_created_idx ON example_items (item_id)",
        "ALTER TABLE example_items ADD COLUMN IF NOT EXISTS note TEXT",
        "ALTER TABLE example_items ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ DEFAULT NOW()",
    ],
)
def test_expand_only_statements_are_allowed(statement: str) -> None:
    assessment = assess_online_sql(statement)
    assert assessment.safe is True


@pytest.mark.parametrize(
    ("statement", "rule"),
    [
        ("DROP TABLE example_items", "destructive-drop"),
        ("ALTER TABLE example_items DROP COLUMN note", "destructive-drop"),
        ("ALTER TABLE example_items RENAME COLUMN note TO body", "destructive-rename"),
        ("TRUNCATE example_items", "destructive-truncate"),
        ("DELETE FROM example_items", "data-delete"),
        ("UPDATE example_items SET note = ''", "data-update"),
        ("INSERT INTO example_items (item_id) VALUES ('x')", "data-insert"),
        ("ALTER TABLE example_items ALTER COLUMN note TYPE JSONB", "alter-column"),
        ("ALTER TABLE example_items ALTER COLUMN note SET NOT NULL", "alter-column"),
        ("CREATE UNIQUE INDEX example_unique_idx ON example_items (item_id)", "unique-index"),
        ("ALTER TABLE example_items ADD COLUMN IF NOT EXISTS note TEXT NOT NULL", "unsafe-add-column"),
        ("ALTER TABLE example_items ADD COLUMN IF NOT EXISTS owner TEXT REFERENCES hero_users(phone_hash)", "unsafe-add-column"),
        ("CREATE TABLE example_items (item_id TEXT)", "unsupported-online-ddl"),
        ("CREATE TABLE IF NOT EXISTS a (id TEXT); DROP TABLE a", "multiple-statements"),
    ],
)
def test_unsafe_or_non_idempotent_statements_are_rejected(statement: str, rule: str) -> None:
    assessment = assess_online_sql(statement)
    assert assessment.safe is False
    assert assessment.rule == rule


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "database_migrations.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_repository_migration_source_passes_static_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    assert validate_migration_source(root / "database_migrations.py") == []


def test_static_policy_rejects_contract_phase_and_drop(tmp_path: Path) -> None:
    source = """
NAME = 'unsafe_contract'
def _apply_one(store, executor):
    executor.execute('DROP TABLE hero_users')
_MIGRATIONS = (
    MigrationSpec(version=1, name=NAME, checksum='x', apply=_apply_one, phase='contract', legacy_bootstrap=True),
)
"""
    rules = {item.rule for item in validate_migration_source(_write(tmp_path, source))}
    assert rules == {"non-expand-phase", "destructive-drop"}


def test_static_policy_rejects_dynamic_and_raw_sql(tmp_path: Path) -> None:
    source = """
NAME = 'dynamic'
def _apply_one(store, executor):
    table = 'items'
    executor.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT)')
    store.pool.connection().execute('CREATE TABLE IF NOT EXISTS hidden_items (id TEXT)')
_MIGRATIONS = (
    MigrationSpec(version=1, name=NAME, checksum='x', apply=_apply_one, phase='expand', legacy_bootstrap=True),
)
"""
    rules = {item.rule for item in validate_migration_source(_write(tmp_path, source))}
    assert rules == {"dynamic-sql", "raw-sql-execution"}


def test_static_policy_rejects_version_gaps_and_late_legacy_bootstrap(tmp_path: Path) -> None:
    source = """
def _apply_two(store, executor):
    executor.execute('CREATE TABLE IF NOT EXISTS items (id TEXT)')
_MIGRATIONS = (
    MigrationSpec(version=2, name='two', checksum='x', apply=_apply_two, phase='expand', legacy_bootstrap=True),
)
"""
    rules = {item.rule for item in validate_migration_source(_write(tmp_path, source))}
    assert rules == {"version-sequence", "legacy-bootstrap-version"}


def test_runtime_executor_never_calls_postgres_for_unsafe_sql() -> None:
    connection = MagicMock()
    executor = database_migrations.OnlineMigrationExecutor(connection)

    with pytest.raises(database_migrations.SchemaMigrationError) as raised:
        executor.execute("ALTER TABLE hero_users DROP COLUMN profile")

    assert raised.value.code == "unsafe_online_migration_sql"
    connection.execute.assert_not_called()


def test_runtime_executor_delegates_safe_sql_and_parameters() -> None:
    connection = MagicMock()
    executor = database_migrations.OnlineMigrationExecutor(connection)
    statement = "ALTER TABLE hero_users ADD COLUMN IF NOT EXISTS migration_note TEXT"

    result = executor.execute(statement, ("unused",))

    assert result is connection.execute.return_value
    connection.execute.assert_called_once_with(statement, ("unused",))


def test_runtime_registry_rejects_non_expand_phase(monkeypatch) -> None:
    unsafe = database_migrations.MigrationSpec(
        version=1,
        name="unsafe",
        checksum="a" * 64,
        apply=lambda store, executor: (),
        phase="contract",
        legacy_bootstrap=True,
    )
    monkeypatch.setattr(database_migrations, "_MIGRATIONS", (unsafe,))

    with pytest.raises(database_migrations.SchemaMigrationError) as raised:
        database_migrations._validate_registry()
    assert raised.value.code == "unsafe_migration_phase"
