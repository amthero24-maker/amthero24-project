"""Mission Workspace v3 for selecting and updating a specific mission safely."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

_SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}


@dataclass(frozen=True)
class WorkspaceCommand:
    action: str
    selector: str = ""
    field: str = ""
    value: str = ""


@dataclass(frozen=True)
class MissionResolution:
    status: str
    mission: dict[str, Any] | None = None
    matches: tuple[dict[str, Any], ...] = ()


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;#]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in str(phone or "") if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _parse_pattern(text: str, patterns: tuple[str, ...], action: str, field: str = "") -> WorkspaceCommand | None:
    for pattern in patterns:
        match = re.match(pattern, text or "", flags=re.IGNORECASE)
        if not match:
            continue
        groups = tuple(" ".join(str(value or "").split()).strip(" -") for value in match.groups())
        if field:
            selector = groups[0] if groups else ""
            value = groups[1] if len(groups) > 1 else ""
            return WorkspaceCommand(action, selector=selector, field=field, value=value)
        selector = groups[0] if groups else ""
        return WorkspaceCommand(action, selector=selector)
    return None


_SELECT_PATTERNS = (
    r"^\s*(?:افتح|اختار|اختر|اعرض|ورجيني|فرجيني)\s+(?:المهمة|مهمة)\s+(.+?)\s*$",
    r"^\s*(?:تفاصيل|وضع|حالة)\s+(?:المهمة|مهمة)\s+(.+?)\s*$",
    r"^\s*(?:المهمة|مهمة)\s+(\d+)\s*$",
    r"^\s*(?:öffne|oeffne|zeige|wähle|waehle)\s+(?:die\s+)?aufgabe\s+(.+?)\s*$",
    r"^\s*(?:open|show|select)\s+(?:the\s+)?task\s+(.+?)\s*$",
    r"^\s*(?:відкрий|покажи|вибери)\s+завдання\s+(.+?)\s*$",
    r"^\s*(?:ανοιξε|δειξε|επιλεξε)\s+(?:την\s+)?εργασια\s+(.+?)\s*$",
)
_COMPLETE_PATTERNS = (
    r"^\s*(?:خلصت|سكر|سكّر|اغلق|أغلق|انهي|أنهي)\s+(?:المهمة|مهمة)\s+(.+?)\s*$",
    r"^\s*(?:schließe|schliesse|erledige|beende)\s+(?:die\s+)?aufgabe\s+(.+?)\s*$",
    r"^\s*(?:complete|close|finish)\s+(?:the\s+)?task\s+(.+?)\s*$",
    r"^\s*(?:заверши|закрий)\s+завдання\s+(.+?)\s*$",
    r"^\s*(?:ολοκληρωσε|κλεισε)\s+(?:την\s+)?εργασια\s+(.+?)\s*$",
)
_LAST_ACTION_PATTERNS = (
    r"^\s*(?:المهمة|مهمة)\s+(.+?)\s+(?:آخر إجراء|اخر اجراء|آخر خطوة عملتها|اخر خطوة عملتها)\s*[:\-]?\s*(.+)$",
    r"^\s*(?:aufgabe)\s+(.+?)\s+(?:letzte aktion|letzter schritt)\s*[:\-]?\s*(.+)$",
    r"^\s*(?:task)\s+(.+?)\s+(?:last action|last step)\s*[:\-]?\s*(.+)$",
)
_NEXT_STEP_PATTERNS = (
    r"^\s*(?:المهمة|مهمة)\s+(.+?)\s+(?:الخطوة الجاية|الخطوة التالية|الخطوة القادمة)\s*[:\-]?\s*(.+)$",
    r"^\s*(?:aufgabe)\s+(.+?)\s+(?:nächster schritt|naechster schritt)\s*[:\-]?\s*(.+)$",
    r"^\s*(?:task)\s+(.+?)\s+(?:next step)\s*[:\-]?\s*(.+)$",
)
_DUE_PATTERNS = (
    r"^\s*(?:المهمة|مهمة)\s+(.+?)\s+(?:الموعد|المهلة|آخر موعد|اخر موعد)\s*[:\-]?\s*(\d{1,2}[./]\d{1,2}[./]20\d{2}|20\d{2}-\d{1,2}-\d{1,2})\s*$",
    r"^\s*(?:aufgabe)\s+(.+?)\s+(?:frist|termin)\s*[:\-]?\s*(\d{1,2}[./]\d{1,2}[./]20\d{2}|20\d{2}-\d{1,2}-\d{1,2})\s*$",
    r"^\s*(?:task)\s+(.+?)\s+(?:deadline|due date)\s*[:\-]?\s*(\d{1,2}[./]\d{1,2}[./]20\d{2}|20\d{2}-\d{1,2}-\d{1,2})\s*$",
)
_WAITING_PATTERNS = (
    r"^\s*(?:المهمة|مهمة)\s+(.+?)\s+(?:ناطر رد|ناطرة رد|بانتظار الرد|عم استنى الرد|مستني الرد)\s*$",
    r"^\s*(?:aufgabe)\s+(.+?)\s+(?:wartet auf antwort|warte auf antwort|antwort ausstehend)\s*$",
    r"^\s*(?:task)\s+(.+?)\s+(?:waiting for a reply|waiting for reply|awaiting reply)\s*$",
)
_SHOW_SELECTED = {
    "المهمة الحالية", "المهمة المفتوحة", "شو المهمة الحالية", "شو فاتحين هلق",
    "aktuelle aufgabe", "ausgewählte aufgabe", "current task", "selected task",
    "поточне завдання", "вибране завдання", "τρεχουσα εργασια", "επιλεγμενη εργασια",
}
_CLEAR_SELECTED = {
    "سكر المهمة الحالية", "اغلق المهمة الحالية", "ألغي اختيار المهمة", "الغي اختيار المهمة",
    "auswahl aufheben", "keine aufgabe auswählen", "clear task selection", "deselect task",
    "скасуй вибір завдання", "ακυρωσε την επιλογη εργασιας",
}


def detect_workspace_command(text: str) -> WorkspaceCommand | None:
    normalized = _normalize(text)
    if normalized in {_normalize(item) for item in _SHOW_SELECTED}:
        return WorkspaceCommand("show_selected")
    if normalized in {_normalize(item) for item in _CLEAR_SELECTED}:
        return WorkspaceCommand("clear_selection")
    for patterns, action, field in (
        (_LAST_ACTION_PATTERNS, "update", "last_action"),
        (_NEXT_STEP_PATTERNS, "update", "next_step"),
        (_DUE_PATTERNS, "update", "due_at"),
        (_WAITING_PATTERNS, "update", "status"),
        (_COMPLETE_PATTERNS, "complete", ""),
        (_SELECT_PATTERNS, "select", ""),
    ):
        parsed = _parse_pattern(text, patterns, action, field)
        if parsed:
            return parsed
    return None


def _parse_due(value: str) -> str | None:
    iso = re.fullmatch(r"(20\d{2})-(\d{1,2})-(\d{1,2})", value.strip())
    local = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](20\d{2})", value.strip())
    if iso:
        raw = f"{iso.group(1)}-{int(iso.group(2)):02d}-{int(iso.group(3)):02d}"
    elif local:
        raw = f"{local.group(3)}-{int(local.group(2)):02d}-{int(local.group(1)):02d}"
    else:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


class MissionSelectionRepository:
    """Short-lived selection containing only phone hash and mission ID."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS selected_missions (
                        phone_hash TEXT PRIMARY KEY,
                        mission_id TEXT NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS selected_missions_expiry_idx ON selected_missions (expires_at)"
                )

    def select(
        self,
        phone: str,
        mission_id: str,
        *,
        now: datetime | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        expires = current + ttl
        key = _phone_hash(phone)
        clean_id = _clean(mission_id, 64)
        if not clean_id:
            return
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO selected_missions (phone_hash, mission_id, expires_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (phone_hash) DO UPDATE
                    SET mission_id = EXCLUDED.mission_id, expires_at = EXCLUDED.expires_at, updated_at = NOW()
                    """,
                    (key, clean_id, expires),
                )
            return

        def save(data: dict[str, Any]) -> None:
            data.setdefault("selected_missions", {})[key] = {
                "mission_id": clean_id,
                "expires_at": expires.isoformat(),
                "updated_at": current.isoformat(),
            }

        self.store._transaction(save)

    def get(self, phone: str, *, now: datetime | None = None) -> str | None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    "SELECT mission_id, expires_at FROM selected_missions WHERE phone_hash = %s",
                    (key,),
                ).fetchone()
                if not row:
                    return None
                expires = _as_datetime(row["expires_at"])
                if not expires or expires <= current:
                    connection.execute("DELETE FROM selected_missions WHERE phone_hash = %s", (key,))
                    return None
                return str(row["mission_id"])

        record = self.store.snapshot().get("selected_missions", {}).get(key)
        if not isinstance(record, dict):
            return None
        expires = _as_datetime(record.get("expires_at"))
        if not expires or expires <= current:
            self.delete(phone)
            return None
        mission_id = _clean(record.get("mission_id"), 64)
        return mission_id or None

    def delete(self, phone: str) -> bool:
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                cursor = connection.execute("DELETE FROM selected_missions WHERE phone_hash = %s", (key,))
            return cursor.rowcount > 0

        def remove(data: dict[str, Any]) -> bool:
            return data.setdefault("selected_missions", {}).pop(key, None) is not None

        return bool(self.store._transaction(remove))

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                cursor = connection.execute("DELETE FROM selected_missions WHERE expires_at <= %s", (current,))
            return max(cursor.rowcount, 0)

        def cleanup(data: dict[str, Any]) -> int:
            selected = data.setdefault("selected_missions", {})
            expired = [
                key for key, record in selected.items()
                if not isinstance(record, dict)
                or not _as_datetime(record.get("expires_at"))
                or _as_datetime(record.get("expires_at")) <= current
            ]
            for key in expired:
                del selected[key]
            return len(expired)

        return int(self.store._transaction(cleanup))


def resolve_mission(missions: list[dict[str, Any]], selector: str) -> MissionResolution:
    active = [mission for mission in missions if mission.get("status") in {"open", "waiting"}]
    cleaned_selector = _normalize(selector)
    if not cleaned_selector:
        return MissionResolution("missing")
    if cleaned_selector.isdigit():
        index = int(cleaned_selector) - 1
        if 0 <= index < len(active):
            return MissionResolution("found", mission=deepcopy(active[index]))
        return MissionResolution("not_found")

    exact = [
        mission for mission in active
        if _normalize(str(mission.get("title") or "")) == cleaned_selector
        or _normalize(str(mission.get("topic") or "")) == cleaned_selector
    ]
    if len(exact) == 1:
        return MissionResolution("found", mission=deepcopy(exact[0]))
    if len(exact) > 1:
        return MissionResolution("ambiguous", matches=tuple(deepcopy(exact[:5])))

    partial = [
        mission for mission in active
        if cleaned_selector in _normalize(str(mission.get("title") or ""))
        or cleaned_selector in _normalize(str(mission.get("topic") or ""))
    ]
    if len(partial) == 1:
        return MissionResolution("found", mission=deepcopy(partial[0]))
    if len(partial) > 1:
        return MissionResolution("ambiguous", matches=tuple(deepcopy(partial[:5])))
    return MissionResolution("not_found")


class MissionWorkspace:
    def __init__(self, store: Any, memory: Any, selections: MissionSelectionRepository) -> None:
        self.store = store
        self.memory = memory
        self.selections = selections
        self.backend_name = str(getattr(store, "backend_name", "json"))

    def active(self, phone: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.memory.list_missions(phone, status="open", limit=limit)

    def resolve(self, phone: str, selector: str) -> MissionResolution:
        return resolve_mission(self.active(phone), selector)

    def get_by_id(self, phone: str, mission_id: str, *, active_only: bool = True) -> dict[str, Any] | None:
        key = _phone_hash(phone)
        allowed = {"open", "waiting"} if active_only else {"open", "waiting", "completed"}
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT mission_id, title, topic, status, last_action, next_step, due_at,
                           metadata, created_at, updated_at, completed_at
                    FROM hero_missions
                    WHERE phone_hash = %s AND mission_id = %s
                    """,
                    (key, mission_id),
                ).fetchone()
            if not row or row["status"] not in allowed:
                return None
            return self.memory._mission_from_row(row)

        record = self.store.snapshot().get("cases", {}).get(mission_id)
        if not isinstance(record, dict) or record.get("phone_hash") != key or record.get("status") not in allowed:
            return None
        return deepcopy(record)

    def selected(self, phone: str) -> dict[str, Any] | None:
        mission_id = self.selections.get(phone)
        if not mission_id:
            return None
        mission = self.get_by_id(phone, mission_id)
        if not mission:
            self.selections.delete(phone)
        return mission

    def select(self, phone: str, selector: str) -> MissionResolution:
        resolved = self.resolve(phone, selector)
        if resolved.status == "found" and resolved.mission:
            self.selections.select(phone, str(resolved.mission.get("mission_id") or ""))
        return resolved

    def update(
        self,
        phone: str,
        mission_id: str,
        *,
        last_action: str | None = None,
        next_step: str | None = None,
        due_at: str | None = None,
        status: str | None = None,
        operation: str = "updated",
    ) -> dict[str, Any] | None:
        key = _phone_hash(phone)
        clean_last = _clean(last_action, 300) if last_action is not None else None
        clean_next = _clean(next_step, 300) if next_step is not None else None
        clean_due = _parse_due(due_at) if due_at is not None else None
        clean_status = status if status in {"open", "waiting"} else None
        if due_at is not None and clean_due is None:
            return {"_operation": "invalid_due"}

        if self.backend_name == "postgresql":
            assignments: list[str] = []
            values: list[Any] = []
            if clean_last is not None:
                assignments.append("last_action = %s")
                values.append(clean_last)
            if clean_next is not None:
                assignments.append("next_step = %s")
                values.append(clean_next)
            if due_at is not None:
                assignments.append("due_at = %s")
                values.append(clean_due)
            if clean_status is not None:
                assignments.append("status = %s")
                values.append(clean_status)
            if not assignments:
                return self.get_by_id(phone, mission_id)
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    f"""
                    UPDATE hero_missions
                    SET {', '.join(assignments)}, updated_at = NOW()
                    WHERE phone_hash = %s AND mission_id = %s AND status IN ('open', 'waiting')
                    RETURNING mission_id, title, topic, status, last_action, next_step, due_at,
                              metadata, created_at, updated_at, completed_at
                    """,
                    (*values, key, mission_id),
                ).fetchone()
            if not row:
                return None
            result = self.memory._mission_from_row(row)
            result["_operation"] = operation
            return result

        def update_json(data: dict[str, Any]) -> dict[str, Any] | None:
            mission = data.setdefault("cases", {}).get(mission_id)
            if not isinstance(mission, dict) or mission.get("phone_hash") != key or mission.get("status") not in {"open", "waiting"}:
                return None
            if clean_last is not None:
                mission["last_action"] = clean_last
            if clean_next is not None:
                mission["next_step"] = clean_next
            if due_at is not None:
                mission["due_at"] = clean_due
            if clean_status is not None:
                mission["status"] = clean_status
            mission["updated_at"] = datetime.now(UTC).isoformat()
            result = deepcopy(mission)
            result["_operation"] = operation
            return result

        return self.store._transaction(update_json)

    def complete(self, phone: str, mission_id: str) -> dict[str, Any] | None:
        key = _phone_hash(phone)
        current = datetime.now(UTC)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    UPDATE hero_missions
                    SET status = 'completed', completed_at = NOW(), updated_at = NOW()
                    WHERE phone_hash = %s AND mission_id = %s AND status IN ('open', 'waiting')
                    RETURNING mission_id, title, topic, status, last_action, next_step, due_at,
                              metadata, created_at, updated_at, completed_at
                    """,
                    (key, mission_id),
                ).fetchone()
            result = self.memory._mission_from_row(row) if row else None
        else:
            def complete_json(data: dict[str, Any]) -> dict[str, Any] | None:
                mission = data.setdefault("cases", {}).get(mission_id)
                if not isinstance(mission, dict) or mission.get("phone_hash") != key or mission.get("status") not in {"open", "waiting"}:
                    return None
                mission["status"] = "completed"
                mission["completed_at"] = current.isoformat()
                mission["updated_at"] = current.isoformat()
                return deepcopy(mission)

            result = self.store._transaction(complete_json)
        if result and self.selections.get(phone) == mission_id:
            self.selections.delete(phone)
        return result


def status_label(language: str, status: str) -> str:
    lang = language if language in _SUPPORTED_LANGUAGES else "de"
    return {
        "ar": {"open": "مفتوحة", "waiting": "بانتظار الرد", "completed": "مكتملة"},
        "de": {"open": "offen", "waiting": "wartet auf Antwort", "completed": "erledigt"},
        "en": {"open": "open", "waiting": "waiting for reply", "completed": "completed"},
        "uk": {"open": "відкрите", "waiting": "очікує відповіді", "completed": "виконане"},
        "el": {"open": "ανοιχτή", "waiting": "αναμονή απάντησης", "completed": "ολοκληρωμένη"},
    }[lang].get(status, status)


def detail_message(language: str, mission: dict[str, Any], *, selected: bool = True) -> str:
    lang = language if language in _SUPPORTED_LANGUAGES else "de"
    title = str(mission.get("title") or "")
    status = status_label(lang, str(mission.get("status") or "open"))
    labels = {
        "ar": ("المهمة", "الحالة", "آخر إجراء", "الخطوة التالية", "المهلة", "أي تحديث هلق رح ينطبق على هالمهمة."),
        "de": ("Aufgabe", "Status", "Letzte Aktion", "Nächster Schritt", "Frist", "Die nächsten Updates gelten für diese Aufgabe."),
        "en": ("Task", "Status", "Last action", "Next step", "Due date", "The next updates will apply to this task."),
        "uk": ("Завдання", "Статус", "Остання дія", "Наступний крок", "Термін", "Наступні оновлення застосовуватимуться до цього завдання."),
        "el": ("Εργασία", "Κατάσταση", "Τελευταία ενέργεια", "Επόμενο βήμα", "Προθεσμία", "Οι επόμενες ενημερώσεις θα ισχύουν για αυτή την εργασία."),
    }[lang]
    lines = [f"{labels[0]}: {title}", f"{labels[1]}: {status}"]
    if mission.get("last_action"):
        lines.append(f"{labels[2]}: {mission['last_action']}")
    if mission.get("next_step"):
        lines.append(f"{labels[3]}: {mission['next_step']}")
    if mission.get("due_at"):
        lines.append(f"{labels[4]}: {mission['due_at']}")
    if selected:
        lines.append(labels[5])
    return "\n".join(lines)


def resolution_message(language: str, resolution: MissionResolution) -> str:
    lang = language if language in _SUPPORTED_LANGUAGES else "de"
    if resolution.status == "ambiguous":
        heading = {
            "ar": "لقيت أكثر من مهمة بنفس الوصف. اختار الرقم:",
            "de": "Ich habe mehrere passende Aufgaben gefunden. Wähle die Nummer:",
            "en": "I found several matching tasks. Choose the number:",
            "uk": "Знайдено кілька відповідних завдань. Вибери номер:",
            "el": "Βρέθηκαν πολλές σχετικές εργασίες. Επίλεξε αριθμό:",
        }[lang]
        return heading + "\n" + "\n".join(
            f"{index}. {mission.get('title')}" for index, mission in enumerate(resolution.matches, start=1)
        )
    return {
        "ar": "ما لقيت هالمهمة بين مهامك المفتوحة. اكتب «شو مهامي؟» وشوف الرقم الصحيح.",
        "de": "Diese Aufgabe wurde unter deinen offenen Aufgaben nicht gefunden. Schreib „Meine Aufgaben“ und wähle die Nummer.",
        "en": "I could not find that task among your open tasks. Say “my tasks” and choose its number.",
        "uk": "Цього завдання немає серед відкритих. Напиши «мої завдання» та вибери номер.",
        "el": "Δεν βρήκα αυτή την εργασία στις ανοιχτές εργασίες. Γράψε «οι εργασίες μου» και επίλεξε αριθμό.",
    }[lang]


def no_selection_message(language: str) -> str:
    lang = language if language in _SUPPORTED_LANGUAGES else "de"
    return {
        "ar": "ما في مهمة محددة مفتوحة هلق. اكتب «شو مهامي؟» وبعدها «افتح المهمة 2».",
        "de": "Derzeit ist keine bestimmte Aufgabe ausgewählt. Schreib „Meine Aufgaben“ und danach „Öffne Aufgabe 2“.",
        "en": "No specific task is selected. Say “my tasks” and then “open task 2”.",
        "uk": "Конкретне завдання не вибрано. Напиши «мої завдання», а потім «відкрий завдання 2».",
        "el": "Δεν έχει επιλεγεί συγκεκριμένη εργασία. Γράψε «οι εργασίες μου» και μετά «άνοιξε εργασία 2».",
    }[lang]


def selection_cleared_message(language: str) -> str:
    return {
        "ar": "تمام، سكّرت مساحة المهمة الحالية. مهامك نفسها ما انحذفت.",
        "de": "Die aktuelle Auswahl wurde aufgehoben. Deine Aufgaben wurden nicht gelöscht.",
        "en": "The current selection was cleared. Your tasks were not deleted.",
        "uk": "Поточний вибір скасовано. Завдання не видалено.",
        "el": "Η τρέχουσα επιλογή καταργήθηκε. Οι εργασίες δεν διαγράφηκαν.",
    }.get(language, "Task selection cleared.")
