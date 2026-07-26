"""Persistent JSON store tests."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore


def test_store_persists_hashes_phone_and_omits_media_id(tmp_path) -> None:
    path = tmp_path / "store.json"
    store = JsonDataStore(path)
    assert store.claim_message("one", "+49123", "hello", media_id="sensitive-id")
    assert not store.claim_message("one", "+49123")
    saved = JsonDataStore(path).snapshot()
    record = saved["messages"]["one"]
    assert record["phone_hash"] != "+49123"
    assert "media_id" not in record
    assert record["has_media"] is True


def test_concurrent_claims_and_user_updates_remain_valid(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(executor.map(lambda number: store.claim_message(str(number), "49123"), range(30)))
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda number: store.update_user("49123", {"city": f"City {number}"}), range(30)))
    with store.path.open() as file:
        parsed = json.load(file)
    assert len(parsed["messages"]) == 30
    assert store.get_user("49123")["city"].startswith("City ")


def test_cleanup_and_delete_user(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    store.claim_message("old", "49123")
    store.update_user("49123", {"first_name": "Sam"})
    future = datetime.now(UTC) + timedelta(hours=25)
    assert store.cleanup_expired(future) == 1
    assert store.delete_user("49123") is True
    assert store.get_user("49123") == {}
