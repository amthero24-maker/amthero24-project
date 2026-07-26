"""Hero Memory repository tests using the atomic JSON backend."""
from data_store import JsonDataStore
from hero_memory import HeroMemory


def test_missions_are_isolated_by_hashed_user(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    memory = HeroMemory(store)

    first = memory.create_mission("49111", title="WKK invoice", topic="invoice")
    memory.create_mission("49222", title="Bürgeramt appointment", topic="appointment")

    assert first["status"] == "open"
    assert [item["title"] for item in memory.list_missions("49111")] == ["WKK invoice"]
    assert [item["title"] for item in memory.list_missions("49222")] == ["Bürgeramt appointment"]
    snapshot = store.snapshot()
    assert all(record["phone_hash"] not in {"49111", "49222"} for record in snapshot["cases"].values())


def test_complete_export_and_delete_user_memory(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    memory = HeroMemory(store)
    store.update_user("49123", {
        "memory_consent": "granted",
        "first_name": "وسام",
        "preferred_language": "ar",
        "city": "Düsseldorf",
        "last_assistant_reply": "internal context must not be exported",
    })
    memory.record_consent("49123", "granted", "test-v1")
    memory.create_mission("49123", title="فاتورة WKK", topic="invoice", metadata={"source": "whatsapp", "secret": "blocked"})

    completed = memory.complete_latest_mission("49123")
    assert completed is not None and completed["status"] == "completed"
    exported = memory.export_user_data("49123")
    assert exported["profile"]["first_name"] == "وسام"
    assert "last_assistant_reply" not in exported["profile"]
    assert exported["missions"][0]["metadata"] == {"source": "whatsapp"}

    assert memory.delete_all_user_data("49123") is True
    assert store.get_user("49123") == {}
    assert memory.list_missions("49123", status="all") == []


def test_consent_audit_does_not_store_raw_phone(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    memory = HeroMemory(store)
    memory.record_consent("+4912345", "granted", "2026-07-v1")
    event = store.snapshot()["audit_log"][0]
    assert event["phone_hash"] != "+4912345"
    assert event["decision"] == "granted"
