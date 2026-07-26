"""Application-level Mission Workspace v3 tests."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import mission_workspace_extensions
from data_store import JsonDataStore


def _install_store(store: JsonDataStore) -> None:
    mission_workspace_extensions.core.store = store
    mission_workspace_extensions.core._hero_memory_store = mission_workspace_extensions.core.HeroMemory(store)
    mission_workspace_extensions._SELECTION_REPOSITORY = None
    mission_workspace_extensions._WORKSPACE = None


def _seed(store: JsonDataStore) -> tuple[dict, dict]:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "first_name": "وسام",
    })
    memory = mission_workspace_extensions.core.HeroMemory(store)
    older = memory.create_mission("49123", title="WKK Rechnung", topic="invoice")
    newer = memory.create_mission("49123", title="Jobcenter Unterlagen", topic="jobcenter")
    return older, newer


async def _send(store: JsonDataStore, message_id: str, text: str, send: AsyncMock) -> None:
    message = mission_workspace_extensions.core.IncomingMessage(message_id, "49123", text, "text")
    store.claim_message(message_id, "49123", text)
    with patch.object(mission_workspace_extensions.core, "send_whatsapp_message", new=send):
        await mission_workspace_extensions.process_incoming(message)


@pytest.mark.anyio
async def test_select_task_two_then_generic_update_targets_only_that_task(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    older, newer = _seed(store)
    send = AsyncMock()

    await _send(store, "select-1", "افتح المهمة 2", send)
    assert "WKK Rechnung" in send.await_args.args[1]
    assert "أي تحديث هلق" in send.await_args.args[1]

    await _send(store, "update-1", "آخر إجراء: بعتت الإيميل", send)
    memory = mission_workspace_extensions.core._hero_memory()
    older_after = next(item for item in memory.list_missions("49123", status="all", limit=10) if item["mission_id"] == older["mission_id"])
    newer_after = next(item for item in memory.list_missions("49123", status="all", limit=10) if item["mission_id"] == newer["mission_id"])

    assert older_after["last_action"] == "بعتت الإيميل"
    assert not newer_after.get("last_action")
    assert "WKK Rechnung" in send.await_args.args[1]


@pytest.mark.anyio
async def test_multiple_tasks_require_selection_before_generic_completion(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    older, newer = _seed(store)
    send = AsyncMock()

    await _send(store, "complete-no-selection", "خلصت المهمة", send)

    assert "عندك أكثر من مهمة" in send.await_args.args[1]
    missions = mission_workspace_extensions.core._hero_memory().list_missions("49123", status="all", limit=10)
    assert {item["mission_id"]: item["status"] for item in missions} == {
        older["mission_id"]: "open",
        newer["mission_id"]: "open",
    }


@pytest.mark.anyio
async def test_complete_specific_task_by_name_and_clear_selection(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    older, newer = _seed(store)
    send = AsyncMock()

    await _send(store, "complete-specific", "خلصت مهمة WKK Rechnung", send)

    assert "سكّرت مهمة" in send.await_args.args[1]
    workspace = mission_workspace_extensions._workspace()
    assert workspace.get_by_id("49123", older["mission_id"], active_only=False)["status"] == "completed"
    assert workspace.get_by_id("49123", newer["mission_id"])["status"] == "open"
    assert workspace.selected("49123") is None


@pytest.mark.anyio
async def test_specific_due_update_selects_task_for_followup(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    older, _newer = _seed(store)
    send = AsyncMock()

    await _send(store, "due-specific", "مهمة WKK الموعد: 10.08.2099", send)

    updated = mission_workspace_extensions._workspace().get_by_id("49123", older["mission_id"])
    assert updated is not None
    assert updated["due_at"] == "2099-08-10"
    assert mission_workspace_extensions._workspace().selected("49123")["mission_id"] == older["mission_id"]
    assert "2099-08-10" in send.await_args.args[1]


def test_privacy_delete_and_retention_clear_mission_selections(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "workspace-test-secret")
    store = JsonDataStore(tmp_path / "store.json")
    _install_store(store)
    older, _newer = _seed(store)
    mission_workspace_extensions._selection_repository().select("49123", older["mission_id"])

    assert mission_workspace_extensions._privacy_delete_with_selection(store, "49123") is True
    assert mission_workspace_extensions._selection_repository().get("49123") is None
    assert store.get_user("49123") == {}
