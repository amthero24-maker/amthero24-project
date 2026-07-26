"""Mission Workspace v3 composition for safe multi-mission conversations."""
from __future__ import annotations

from typing import Any

import admin_extensions as composed
import privacy_engine as privacy_module
import privacy_extensions as privacy_layer
from mission_workspace import (
    MissionResolution,
    MissionSelectionRepository,
    MissionWorkspace,
    WorkspaceCommand,
    detail_message,
    detect_workspace_command,
    no_selection_message,
    resolution_message,
    selection_cleared_message,
)

core = composed.core
_ORIGINAL_PROCESS_INCOMING = core.process_incoming
_ORIGINAL_PRIVACY_DELETE = privacy_layer.delete_all_user_data
_ORIGINAL_PRIVACY_CLEANUP = privacy_module.cleanup_retention
_SELECTION_REPOSITORY: MissionSelectionRepository | None = None
_WORKSPACE: MissionWorkspace | None = None


def _selection_repository(store: Any | None = None) -> MissionSelectionRepository:
    global _SELECTION_REPOSITORY
    target = store or core.store
    if _SELECTION_REPOSITORY is None or _SELECTION_REPOSITORY.store is not target:
        _SELECTION_REPOSITORY = MissionSelectionRepository(target)
    return _SELECTION_REPOSITORY


def _workspace(store: Any | None = None) -> MissionWorkspace:
    global _WORKSPACE
    target = store or core.store
    if _WORKSPACE is None or _WORKSPACE.store is not target:
        memory = core.HeroMemory(target)
        _WORKSPACE = MissionWorkspace(target, memory, _selection_repository(target))
    return _WORKSPACE


def _language(profile: dict[str, Any]) -> str:
    language = str(
        profile.get("preferred_language")
        if profile.get("memory_consent") == "granted"
        else profile.get("session_language") or profile.get("preferred_language") or "de"
    )
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


def _choose_first_message(language: str) -> str:
    return {
        "ar": "عندك أكثر من مهمة مفتوحة، وما بدي أعدّل الغلط. اكتب «شو مهامي؟» وبعدها «افتح المهمة 2».",
        "de": "Du hast mehrere offene Aufgaben. Damit ich nicht die falsche ändere, schreib „Meine Aufgaben“ und danach „Öffne Aufgabe 2“.",
        "en": "You have several open tasks. To avoid updating the wrong one, say “my tasks” and then “open task 2”.",
        "uk": "У тебе кілька відкритих завдань. Щоб не змінити не те, напиши «мої завдання», а потім «відкрий завдання 2».",
        "el": "Έχεις πολλές ανοιχτές εργασίες. Για να μην ενημερώσω λάθος εργασία, γράψε «οι εργασίες μου» και μετά «άνοιξε εργασία 2».",
    }.get(language, "Please select a task first.")


def _invalid_due_message(language: str) -> str:
    return {
        "ar": "التاريخ مو واضح. اكتبه مثل 10.08.2026.",
        "de": "Das Datum ist nicht eindeutig. Schreib es zum Beispiel als 10.08.2026.",
        "en": "The date is unclear. Write it like 10.08.2026.",
        "uk": "Дата незрозуміла. Напиши її, наприклад, як 10.08.2026.",
        "el": "Η ημερομηνία δεν είναι σαφής. Γράψε την, π.χ. 10.08.2026.",
    }.get(language, "The date is unclear.")


def _apply_update(phone: str, mission: dict[str, Any], field: str, value: str) -> dict[str, Any] | None:
    workspace = _workspace()
    mission_id = str(mission.get("mission_id") or "")
    if field == "last_action":
        return workspace.update(phone, mission_id, last_action=value, operation="last_action")
    if field == "next_step":
        return workspace.update(phone, mission_id, next_step=value, operation="next_step")
    if field == "due_at":
        return workspace.update(phone, mission_id, due_at=value, operation="due")
    if field == "status":
        return workspace.update(phone, mission_id, status="waiting", operation="waiting")
    return mission


def _selected_mutation(intent: Any) -> tuple[str, str] | None:
    if not intent or str(getattr(intent, "action", "")) != "create":
        return None
    title = str(getattr(intent, "title", "") or "")
    prefixes = (
        ("@mission-next-step:", "next_step"),
        ("@mission-last-action:", "last_action"),
        ("@mission-due:", "due_at"),
    )
    for prefix, field in prefixes:
        if title.startswith(prefix):
            return field, title[len(prefix):].strip()
    if title == "@mission-status:waiting":
        return "status", "waiting"
    return None


async def _handle_explicit(
    message: Any,
    command: WorkspaceCommand,
    profile: dict[str, Any],
    language: str,
) -> bool:
    if profile.get("memory_consent") != "granted":
        await core._finish(message.message_id, core.memory_required_message(language), message.sender)
        return True
    workspace = _workspace()

    if command.action == "clear_selection":
        _selection_repository().delete(message.sender)
        await core._finish(message.message_id, selection_cleared_message(language), message.sender)
        return True

    if command.action == "show_selected":
        selected = workspace.selected(message.sender)
        reply = detail_message(language, selected) if selected else no_selection_message(language)
        await core._finish(message.message_id, reply, message.sender)
        return True

    resolution = workspace.resolve(message.sender, command.selector)
    if resolution.status != "found" or not resolution.mission:
        await core._finish(message.message_id, resolution_message(language, resolution), message.sender)
        return True

    mission = resolution.mission
    _selection_repository().select(message.sender, str(mission.get("mission_id") or ""))
    if command.action == "select":
        await core._finish(message.message_id, detail_message(language, mission), message.sender)
        return True
    if command.action == "complete":
        completed = workspace.complete(message.sender, str(mission.get("mission_id") or ""))
        await core._finish(message.message_id, core.mission_completed_message(language, completed), message.sender)
        return True
    if command.action == "update":
        updated = _apply_update(message.sender, mission, command.field, command.value)
        if updated and updated.get("_operation") == "invalid_due":
            await core._finish(message.message_id, _invalid_due_message(language), message.sender)
        else:
            await core._finish(message.message_id, core.mission_created_message(language, updated or {"_operation": "missing"}), message.sender)
        return True
    return False


async def process_incoming(message: Any) -> None:
    if message.message_type != "text":
        await _ORIGINAL_PROCESS_INCOMING(message)
        return

    profile = core.store.get_user(message.sender)
    language = _language(profile)
    stage = str(profile.get("onboarding_stage") or "")
    explicit = detect_workspace_command(message.text)
    if explicit and stage == "complete":
        if await _handle_explicit(message, explicit, profile, language):
            return

    if profile.get("memory_consent") == "granted" and stage == "complete":
        intent = core.detect_mission_intent(message.text)
        selected_update = _selected_mutation(intent)
        is_complete = bool(intent and str(getattr(intent, "action", "")) == "complete")
        if selected_update or is_complete:
            workspace = _workspace()
            selected = workspace.selected(message.sender)
            if not selected:
                active = workspace.active(message.sender)
                if len(active) > 1:
                    await core._finish(message.message_id, _choose_first_message(language), message.sender)
                    return
                if len(active) == 1:
                    selected = active[0]
                    _selection_repository().select(message.sender, str(selected.get("mission_id") or ""))
            if selected:
                if is_complete:
                    completed = workspace.complete(message.sender, str(selected.get("mission_id") or ""))
                    await core._finish(message.message_id, core.mission_completed_message(language, completed), message.sender)
                    return
                field, value = selected_update or ("", "")
                updated = _apply_update(message.sender, selected, field, value)
                if updated and updated.get("_operation") == "invalid_due":
                    await core._finish(message.message_id, _invalid_due_message(language), message.sender)
                else:
                    await core._finish(message.message_id, core.mission_created_message(language, updated or {"_operation": "missing"}), message.sender)
                return

    await _ORIGINAL_PROCESS_INCOMING(message)


def _privacy_delete_with_selection(store: Any, phone: str) -> bool:
    selection_deleted = _selection_repository(store).delete(phone)
    return bool(_ORIGINAL_PRIVACY_DELETE(store, phone) or selection_deleted)


def _privacy_cleanup_with_selection(store: Any, **kwargs: Any) -> dict[str, int]:
    result = _ORIGINAL_PRIVACY_CLEANUP(store, **kwargs)
    result["mission_selections"] = _selection_repository(store).cleanup_expired(now=kwargs.get("now"))
    return result


privacy_layer.delete_all_user_data = _privacy_delete_with_selection
privacy_module.cleanup_retention = _privacy_cleanup_with_selection
core.process_incoming = process_incoming

app = composed.app
store = composed.store
