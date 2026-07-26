"""Production durable-storage policy tests."""
from __future__ import annotations

import pytest

import storage_factory
from data_store import JsonDataStore


def test_local_runtime_without_database_uses_json(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_FALLBACK_ALLOWED", raising=False)

    store = storage_factory.create_runtime_store(tmp_path / "local.json")

    assert isinstance(store, JsonDataStore)
    assert store.backend_name == "json"


def test_configured_database_runs_schema_before_json_import(tmp_path, monkeypatch) -> None:
    calls: dict[str, object] = {"order": []}

    class FakePostgresStore:
        backend_name = "postgresql"

        def __init__(self, database_url: str) -> None:
            calls["database_url"] = database_url

        def migrate_json(self, path) -> int:
            calls["order"].append("json")
            calls["migration_path"] = path
            return 0

        def close(self) -> None:
            calls["closed"] = True

    def migrate_schema(store, *, app_version: str):
        calls["order"].append("schema")
        calls["app_version"] = app_version
        return object()

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.internal/amthero24")
    monkeypatch.setattr(storage_factory, "PostgresDataStore", FakePostgresStore)
    monkeypatch.setattr(storage_factory, "run_database_migrations", migrate_schema)

    store = storage_factory.create_runtime_store(tmp_path / "store.json")

    assert store.backend_name == "postgresql"
    assert calls == {
        "order": ["schema", "json"],
        "database_url": "postgresql://db.internal/amthero24",
        "app_version": storage_factory.APP_VERSION,
        "migration_path": tmp_path / "store.json",
    }


def test_database_failure_is_fail_closed_by_default(tmp_path, monkeypatch) -> None:
    class BrokenPostgresStore:
        def __init__(self, database_url: str) -> None:
            raise RuntimeError("database unavailable")

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.internal/amthero24")
    monkeypatch.delenv("DATABASE_FALLBACK_ALLOWED", raising=False)
    monkeypatch.setattr(storage_factory, "PostgresDataStore", BrokenPostgresStore)

    with pytest.raises(storage_factory.StorageInitializationError, match="refusing unsafe JSON fallback"):
        storage_factory.create_runtime_store(tmp_path / "unsafe-fallback.json")


def test_emergency_fallback_requires_explicit_operator_flag(tmp_path, monkeypatch) -> None:
    class BrokenPostgresStore:
        def __init__(self, database_url: str) -> None:
            raise RuntimeError("database unavailable")

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.internal/amthero24")
    monkeypatch.setenv("DATABASE_FALLBACK_ALLOWED", "true")
    monkeypatch.setattr(storage_factory, "PostgresDataStore", BrokenPostgresStore)

    store = storage_factory.create_runtime_store(tmp_path / "emergency.json")

    assert isinstance(store, JsonDataStore)
    assert store.backend_name == "json"


def test_schema_incompatibility_never_falls_back_to_json(tmp_path, monkeypatch) -> None:
    calls = {"closed": False}

    class FakePostgresStore:
        backend_name = "postgresql"

        def __init__(self, database_url: str) -> None:
            return None

        def close(self) -> None:
            calls["closed"] = True

    def reject_schema(store, *, app_version: str):
        raise storage_factory.SchemaMigrationError("database_schema_ahead")

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.internal/amthero24")
    monkeypatch.setenv("DATABASE_FALLBACK_ALLOWED", "true")
    monkeypatch.setattr(storage_factory, "PostgresDataStore", FakePostgresStore)
    monkeypatch.setattr(storage_factory, "run_database_migrations", reject_schema)

    with pytest.raises(storage_factory.StorageInitializationError, match="schema is incompatible"):
        storage_factory.create_runtime_store(tmp_path / "must-not-fallback.json")
    assert calls["closed"] is True


def test_production_entrypoint_installer_routes_json_constructor_through_policy(tmp_path, monkeypatch) -> None:
    original_new = JsonDataStore.__new__
    sentinel = object()
    monkeypatch.setattr(storage_factory, "_POLICY_INSTALLED", False)
    monkeypatch.setattr(storage_factory, "create_runtime_store", lambda path: sentinel)

    try:
        storage_factory.install_production_storage_policy()
        result = JsonDataStore(tmp_path / "ignored.json")
        assert result is sentinel
        assert storage_factory._POLICY_INSTALLED is True
    finally:
        JsonDataStore.__new__ = original_new
        storage_factory._POLICY_INSTALLED = False
