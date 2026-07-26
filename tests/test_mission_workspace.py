"""Mission Workspace v3 pure and repository tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore
from hero_memory import HeroMemory
from mission_workspace import (
    MissionSelectionRepository,
    MissionWorkspace,
    detect_workspace_command,
    resolve_mission,
)


def test_detects_specific_select_complete_and_update_commands() -> None:
    selected = detect_workspace_command("افتح المهمة 2")
    assert selected is not None
    assert (selected.action, selected.selector) == ("select", "2")

    completed = detect_workspace_command("خلصت مهمة WKK")
    assert completed is not None
    assert (completed.action, completed.selector) == ("complete", "WKK")

    updated = detect_workspace_command("المهمة 2 آخر إجراء: بعتت الإيميل")
    assert updated is not None
    assert (updated.action, updated.selector, updated.field, updated.value) == (
        "update", "2", "last_action", "بعتت الإيميل"
    )

    due = detect_workspace_command("task WKK deadline: 10.08.2026")
    assert due is not None
    assert (due.action, due.selector, due.field, due.value) == (
        "update", "WKK", "due_at", "10.08.2026"
    )


def test_resolves_by_list_number_exact_title_and_detects_ambiguity() -> None:
    missions = [
        {"mission_id": "m2", "title": "WKK Rechnung", "topic": "invoice", "status": "open"},
        {"mission_id": "m1", "title": "Jobcenter Unterlagen", "topic": "jobcenter", "status": "waiting"},
        {"mission_id": "done", "title": "Alte WKK", "topic": "invoice", "status": "completed"},
    ]
    assert resolve_mission(missions, "2").mission["mission_id"] == "m1"
    assert resolve_mission(missions, "Jobcenter Unterlagen").mission["mission_id"] == "m1"
    ambiguous = resolve_mission(missions, "invoice")
    assert ambiguous.status == "found"
    assert ambiguous.mission["mission_id"] == "m2"

    duplicated = missions[:2] + [
        {"mission_id": "m3", "title": "WKK Beitrag", "topic": "health", "status": "open"}
    ]
    assert resolve_mission(duplicated, "WKK").status == "ambiguous"


def test_selection_is_hashed_expiring_and_contains_no_phone(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    repository = MissionSelectionRepository(store)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    repository.select("491234567", "mission-1", now=now, ttl=timedelta(minutes=5))

    assert repository.get("491234567", now=now + timedelta(minutes=1)) == "mission-1"
    serialized = (tmp_path / "store.json").read_text(encoding="utf-8")
    assert "491234567" not in serialized
    assert repository.get("491234567", now=now + timedelta(minutes=6)) is None


def test_workspace_updates_only_selected_mission_and_completes_it(tmp_path) -> None:
    phone = "49123"
    store = JsonDataStore(tmp_path / "store.json")
    memory = HeroMemory(store)
    first = memory.create_mission(phone, title="WKK", topic="invoice")
    second = memory.create_mission(phone, title="Jobcenter", topic="jobcenter")
    selections = MissionSelectionRepository(store)
    workspace = MissionWorkspace(store, memory, selections)

    resolved = workspace.select(phone, "2")
    assert resolved.status == "found"
    assert resolved.mission["mission_id"] == first["mission_id"]

    updated = workspace.update(
        phone,
        first["mission_id"],
        last_action="E-Mail gesendet",
        next_step="Antwort abwarten",
        status="waiting",
        operation="waiting",
    )
    assert updated is not None
    assert updated["status"] == "waiting"
    assert updated["last_action"] == "E-Mail gesendet"

    untouched = workspace.get_by_id(phone, second["mission_id"])
    assert untouched is not None
    assert untouched["status"] == "open"
    assert not untouched.get("last_action")

    completed = workspace.complete(phone, first["mission_id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert workspace.selected(phone) is None
