"""Hero Memory repository tests using the atomic JSON backend."""
import pytest

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


def test_update_latest_mission_progress_and_deadline(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    memory = HeroMemory(store)
    memory.create_mission("49123", title="فاتورة WKK", topic="invoice")

    next_step = memory.create_mission("49123", title="@mission-next-step:ابعت الإيميل")
    assert next_step["_operation"] == "next_step"
    assert next_step["next_step"] == "ابعت الإيميل"

    last_action = memory.create_mission("49123", title="@mission-last-action:جهزت الاعتراض")
    assert last_action["last_action"] == "جهزت الاعتراض"

    waiting = memory.create_mission("49123", title="@mission-status:waiting")
    assert waiting["status"] == "waiting"
    assert memory.list_missions("49123", status="open")[0]["status"] == "waiting"

    due = memory.create_mission("49123", title="@mission-due:2026-08-10")
    assert due["due_at"] == "2026-08-10"

    completed = memory.complete_latest_mission("49123")
    assert completed is not None and completed["status"] == "completed"


def test_update_without_open_mission_is_safe(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    memory = HeroMemory(JsonDataStore(tmp_path / "store.json"))
    result = memory.create_mission("49123", title="@mission-next-step:انتظر الرد")
    assert result == {"_operation": "missing"}


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
    memory.create_mission(
        "49123",
        title="فاتورة WKK",
        topic="invoice",
        next_step="انتظر الرد",
        due_at="2026-08-10",
        metadata={"source": "whatsapp", "secret": "blocked"},
    )

    completed = memory.complete_latest_mission("49123")
    assert completed is not None and completed["status"] == "completed"
    exported = memory.export_user_data("49123")
    assert exported["profile"]["first_name"] == "وسام"
    assert "last_assistant_reply" not in exported["profile"]
    assert exported["missions"][0]["metadata"] == {"source": "whatsapp"}
    assert exported["missions"][0]["next_step"] == "انتظر الرد"
    assert exported["missions"][0]["due_at"] == "2026-08-10"

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


def test_idempotent_mission_replay_and_payload_conflict(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    memory = HeroMemory(store)
    key = "a" * 64

    first = memory.create_mission(
        "49123",
        title="Track deadline",
        due_at="2026-08-10",
        idempotency_key=key,
    )
    replay = memory.create_mission(
        "49123",
        title="Track deadline",
        due_at="2026-08-10",
        idempotency_key=key,
    )

    assert first["_operation"] == "created"
    assert replay["_operation"] == "replayed"
    assert replay["mission_id"] == first["mission_id"]
    assert len(store.snapshot()["cases"]) == 1

    with pytest.raises(ValueError, match="mission_idempotency_conflict"):
        memory.create_mission(
            "49123",
            title="Changed title",
            due_at="2026-08-10",
            idempotency_key=key,
        )


@pytest.mark.parametrize("key", ["", "A" * 64, "z" * 64, "a" * 63])
def test_invalid_mission_idempotency_key_is_rejected(
    key,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    memory = HeroMemory(JsonDataStore(tmp_path / "store.json"))

    with pytest.raises(ValueError, match="mission_idempotency_key_invalid"):
        memory.create_mission("49123", title="Task", idempotency_key=key)
