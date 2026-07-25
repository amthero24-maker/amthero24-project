"""Persistent JSON store tests."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from data_store import JsonDataStore

def test_store_persists_and_hashes_phone(tmp_path) -> None:
    path = tmp_path / "store.json"
    store = JsonDataStore(path)
    assert store.claim_message("one", "+49123")
    assert not store.claim_message("one", "+49123")
    saved = JsonDataStore(path).snapshot()
    assert saved["messages"]["one"]["phone_hash"] != "+49123"

def test_concurrent_writes_remain_valid_json(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(executor.map(lambda number: store.claim_message(str(number), "49123"), range(30)))
    with store.path.open() as file:
        assert len(json.load(file)["messages"]) == 30

def test_cleanup_removes_expired_messages(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    store.claim_message("old", "49123")
    future = datetime.now(UTC) + timedelta(hours=25)
    assert store.cleanup_expired(future) == 1
    assert store.snapshot()["messages"] == {}
