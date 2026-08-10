"""Real PostgreSQL tests for versioned, cross-replica schema migrations."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import database_migrations
from data_store import PostgresDataStore
from database_migrations import (
    LATEST_SCHEMA_VERSION,
    OnlineMigrationExecutor,
    SchemaMigrationError,
    migration_lock_key,
    run_database_migrations,
    validate_schema_contract,
)


@pytest.fixture
def isolated_schema() -> str:
    base_url = os.environ["DATABASE_URL"]
    schema = f"migration_test_{uuid4().hex}"
    with psycopg.connect(base_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    values = conninfo_to_dict(base_url)
    existing_options = str(values.pop("options", "") or "").strip()
    options = f"{existing_options} -csearch_path={schema}".strip()
    scoped_url = make_conninfo(**values, options=options)
    try:
        yield scoped_url
    finally:
        with psycopg.connect(base_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )


def test_fresh_schema_is_applied_once_and_recorded(isolated_schema: str) -> None:
    store = PostgresDataStore(isolated_schema)
    try:
        first = run_database_migrations(store, app_version="4.7.0")
        second = run_database_migrations(store, app_version="4.7.0")

        assert first.status == "current"
        assert first.current_version == LATEST_SCHEMA_VERSION == 2
        assert first.applied_versions == (1, 2)
        assert second.applied_versions == ()
        assert second.schema_checksum == first.schema_checksum
        assert "closed_beta_admission" in first.components

        with store.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT version, name, checksum, app_version
                FROM amthero_schema_migrations
                ORDER BY version
                """
            ).fetchall()
            valid, missing = validate_schema_contract(connection)
        assert [int(row["version"]) for row in rows] == [1, 2]
        assert [row["name"] for row in rows] == [
            "production_schema_v1",
            "closed_beta_admission_schema_v2",
        ]
        assert rows[0]["checksum"] == database_migrations._SCHEMA_V1_CHECKSUM
        assert rows[0]["app_version"] == "4.7.0"
        assert rows[1]["app_version"] == "4.7.0"
        assert all(len(str(row["checksum"])) == 64 for row in rows)
        assert valid is True
        assert missing == ()
    finally:
        store.close()


def test_existing_production_v1_checksum_upgrades_only_to_v2(isolated_schema: str) -> None:
    store = PostgresDataStore(isolated_schema)
    try:
        with store.pool.connection() as connection:
            database_migrations._ensure_ledger(connection)
            components = database_migrations._MIGRATIONS[0].apply(
                store,
                OnlineMigrationExecutor(connection),
            )
            connection.execute(
                """
                INSERT INTO amthero_schema_migrations
                    (version, name, checksum, app_version)
                VALUES (1, %s, %s, '4.7.0')
                """,
                (
                    database_migrations._SCHEMA_V1_NAME,
                    "b79ba86b0703b775ba29b6321c73ae9227f327f52cd53ff518a921e5f9b67c5a",
                ),
            )
        assert "hero_memory" in components

        report = run_database_migrations(store, app_version="4.7.0")

        assert report.applied_versions == (2,)
        assert report.current_version == 2
        with store.pool.connection() as connection:
            rows = connection.execute(
                "SELECT version, checksum FROM amthero_schema_migrations ORDER BY version"
            ).fetchall()
            relation = connection.execute(
                "SELECT to_regclass('closed_beta_admissions') AS relation"
            ).fetchone()
        assert [int(row["version"]) for row in rows] == [1, 2]
        assert rows[0]["checksum"] == database_migrations._SCHEMA_V1_CHECKSUM
        assert relation["relation"] is not None
    finally:
        store.close()


def test_two_replicas_serialize_the_first_migration(isolated_schema: str) -> None:
    first_store = PostgresDataStore(isolated_schema)
    second_store = PostgresDataStore(isolated_schema)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reports = list(executor.map(
                lambda store: run_database_migrations(store, app_version="4.7.0"),
                (first_store, second_store),
            ))

        assert all(report.status == "current" for report in reports)
        assert sum(len(report.applied_versions) for report in reports) == 2
        with first_store.pool.connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM amthero_schema_migrations"
            ).fetchone()
        assert int(count["count"]) == 2
    finally:
        first_store.close()
        second_store.close()


def test_database_ahead_of_application_fails_closed(isolated_schema: str) -> None:
    store = PostgresDataStore(isolated_schema)
    try:
        run_database_migrations(store, app_version="4.7.0")
        with store.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO amthero_schema_migrations (version, name, checksum, app_version)
                VALUES (999, 'future_schema', %s, '99.0.0')
                """,
                ("f" * 64,),
            )

        with pytest.raises(SchemaMigrationError) as raised:
            run_database_migrations(store, app_version="4.7.0")
        assert raised.value.code == "database_schema_ahead"
    finally:
        store.close()


def test_modified_historical_checksum_fails_closed(isolated_schema: str) -> None:
    store = PostgresDataStore(isolated_schema)
    try:
        run_database_migrations(store, app_version="4.7.0")
        with store.pool.connection() as connection:
            connection.execute(
                "UPDATE amthero_schema_migrations SET checksum = %s WHERE version = 1",
                ("0" * 64,),
            )

        with pytest.raises(SchemaMigrationError) as raised:
            run_database_migrations(store, app_version="4.7.0")
        assert raised.value.code == "migration_checksum_mismatch"
    finally:
        store.close()


def test_lock_wait_is_bounded(isolated_schema: str, monkeypatch) -> None:
    store = PostgresDataStore(isolated_schema)
    holder = psycopg.connect(isolated_schema)
    try:
        holder.execute("SELECT pg_advisory_lock(%s)", (migration_lock_key(),))
        monkeypatch.setenv("SCHEMA_MIGRATION_LOCK_TIMEOUT_SECONDS", "1")

        with pytest.raises(SchemaMigrationError) as raised:
            run_database_migrations(store, app_version="4.7.0")
        assert raised.value.code == "migration_lock_timeout"
    finally:
        holder.execute("SELECT pg_advisory_unlock(%s)", (migration_lock_key(),))
        holder.close()
        store.close()


def test_missing_v2_critical_column_blocks_current_schema(isolated_schema: str) -> None:
    store = PostgresDataStore(isolated_schema)
    try:
        run_database_migrations(store, app_version="4.7.0")
        with store.pool.connection() as connection:
            connection.execute(
                "ALTER TABLE closed_beta_admissions DROP COLUMN consent_version"
            )

        with pytest.raises(SchemaMigrationError) as raised:
            run_database_migrations(store, app_version="4.7.0")
        assert raised.value.code == "schema_contract_incomplete"
    finally:
        store.close()


def test_runtime_executor_rejects_destructive_sql_before_postgres(isolated_schema: str) -> None:
    store = PostgresDataStore(isolated_schema)
    try:
        with store.pool.connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS policy_probe (probe_id TEXT PRIMARY KEY, note TEXT)"
            )
            executor = OnlineMigrationExecutor(connection)
            with pytest.raises(SchemaMigrationError) as raised:
                executor.execute("ALTER TABLE policy_probe DROP COLUMN note")
            columns = connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'policy_probe'
                ORDER BY column_name
                """
            ).fetchall()
        assert raised.value.code == "unsafe_online_migration_sql"
        assert [row["column_name"] for row in columns] == ["note", "probe_id"]
    finally:
        store.close()
