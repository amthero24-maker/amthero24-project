"""Persistent storage tests."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import data_store
from data_store import JsonDataStore


def test_store_persists_hashes_phone_and_omits_media_id(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "store.json"
    store = JsonDataStore(path)
    assert store.backend_name == "json"
    assert store.claim_message("one", "+49123", "hello", media_id="sensitive-id")
    assert not store.claim_message("one", "+49123")
    saved = JsonDataStore(path).snapshot()
    record = saved["messages"]["one"]
    assert record["phone_hash"] != "+49123"
    assert "media_id" not in record
    assert record["has_media"] is True


def test_concurrent_claims_and_user_updates_remain_valid(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(executor.map(lambda number: store.claim_message(str(number), "49123"), range(30)))
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda number: store.update_user("49123", {"city": f"City {number}"}), range(30)))
    with store.path.open() as file:
        parsed = json.load(file)
    assert len(parsed["messages"]) == 30
    assert store.get_user("49123")["city"].startswith("City ")


def test_cleanup_and_delete_user(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    store.claim_message("old", "49123")
    store.update_user("49123", {"first_name": "Sam"})
    future = datetime.now(UTC) + timedelta(hours=25)
    assert store.cleanup_expired(future) == 1
    assert store.delete_user("49123") is True
    assert store.get_user("49123") == {}


def test_user_update_allowlist_rejects_sensitive_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    profile = store.update_user("49123", {
        "first_name": "A" * 200,
        "city": "Düsseldorf",
        "bank_account": "DE00 secret",
        "passport_number": "secret",
    })
    assert profile["first_name"] == "A" * 80
    assert profile["city"] == "Düsseldorf"
    assert "bank_account" not in profile
    assert "passport_number" not in profile


def test_database_url_selects_postgres_backend_and_migrates_json(tmp_path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakePostgresStore:
        backend_name = "postgresql"

        def __init__(self, database_url: str) -> None:
            calls["database_url"] = database_url

        def migrate_json(self, path) -> int:
            calls["migration_path"] = path
            return 0

    monkeypatch.setattr(data_store, "_POSTGRES_SINGLETON", None)
    monkeypatch.setattr(data_store, "PostgresDataStore", FakePostgresStore)
    monkeypatch.setenv("DATABASE_URL", "postgresql://private.example/amthero24")

    store = data_store.JsonDataStore(tmp_path / "store.json")

    assert store.backend_name == "postgresql"
    assert calls["database_url"] == "postgresql://private.example/amthero24"
    assert calls["migration_path"] == tmp_path / "store.json"
    monkeypatch.setattr(data_store, "_POSTGRES_SINGLETON", None)


def test_postgres_failure_falls_back_to_json(tmp_path, monkeypatch) -> None:
    class BrokenPostgresStore:
        def __init__(self, database_url: str) -> None:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(data_store, "_POSTGRES_SINGLETON", None)
    monkeypatch.setattr(data_store, "PostgresDataStore", BrokenPostgresStore)
    monkeypatch.setenv("DATABASE_URL", "postgresql://private.example/amthero24")

    store = data_store.JsonDataStore(tmp_path / "fallback.json")

    assert isinstance(store, data_store.JsonDataStore)
    assert store.backend_name == "json"
    monkeypatch.setattr(data_store, "_POSTGRES_SINGLETON", None)
