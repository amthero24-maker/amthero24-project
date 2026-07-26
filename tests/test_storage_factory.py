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


def test_configured_database_uses_postgres_and_runs_migration(tmp_path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakePostgresStore:
        backend_name = "postgresql"

        def __init__(self, database_url: str) -> None:
            calls["database_url"] = database_url

        def migrate_json(self, path) -> int:
            calls["migration_path"] = path
            return 0

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.internal/amthero24")
    monkeypatch.setattr(storage_factory, "PostgresDataStore", FakePostgresStore)

    store = storage_factory.create_runtime_store(tmp_path / "store.json")

    assert store.backend_name == "postgresql"
    assert calls == {
        "database_url": "postgresql://db.internal/amthero24",
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
