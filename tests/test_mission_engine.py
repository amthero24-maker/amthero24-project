"""Mission command parser tests."""
from mission_engine import (
    detect_mission_intent,
    mission_completed_message,
    mission_created_message,
    mission_list_message,
    mission_title,
)


def test_detect_arabic_mission_actions() -> None:
    create = detect_mission_intent("تابعلي هالموضوع فاتورة WKK")
    assert create is not None and create.action == "create"
    assert "فاتورة" in create.title
    assert detect_mission_intent("شو مهامي؟").action == "list"
    assert detect_mission_intent("وين وصلنا؟").action == "list"
    assert detect_mission_intent("خلصت المهمة").action == "complete"


def test_detect_structured_mission_updates() -> None:
    next_step = detect_mission_intent("الخطوة الجاية: ابعت الإيميل")
    assert next_step is not None and next_step.title == "@mission-next-step:ابعت الإيميل"

    last_action = detect_mission_intent("آخر إجراء: بعتت الاعتراض")
    assert last_action is not None and last_action.title == "@mission-last-action:بعتت الاعتراض"

    waiting = detect_mission_intent("هلأ ناطر رد")
    assert waiting is not None and waiting.title == "@mission-status:waiting"

    due = detect_mission_intent("المهلة 10.08.2026")
    assert due is not None and due.title == "@mission-due:2026-08-10"

    german_due = detect_mission_intent("Die Frist ist am 20.08.2026")
    assert german_due is not None and german_due.title == "@mission-due:2026-08-20"


def test_appointment_help_with_date_is_not_silently_consumed_as_mission_update() -> None:
    request = (
        "Mein Termin beim Bürgeramt ist am 20.08.2026 um 10:00 Uhr in Aachen. "
        "Erstelle eine Vorbereitungsliste."
    )
    assert detect_mission_intent(request) is None


def test_invalid_deadline_does_not_create_update() -> None:
    assert detect_mission_intent("المهلة 32.15.2026") is None


def test_mission_title_uses_topic_or_previous_message() -> None:
    intent = detect_mission_intent("تابعلي هالموضوع")
    assert intent is not None
    assert mission_title(intent, current_topic="invoice") == "invoice"
    assert mission_title(intent, last_message="رسالة من WKK") == "رسالة من WKK"


def test_localized_mission_replies_are_whatsapp_friendly() -> None:
    missions = [{
        "title": "فاتورة WKK",
        "status": "waiting",
        "last_action": "بعتت الإيميل",
        "next_step": "راجع الرد",
        "due_at": "2026-08-10",
    }]
    listed = mission_list_message("ar", missions)
    assert "مهامك المفتوحة" in listed
    assert "فاتورة WKK" in listed
    assert "بانتظار الرد" in listed
    assert "راجع الرد" in listed
    assert "2026-08-10" in listed
    completed = mission_completed_message("ar", missions[0])
    assert "مكتملة" in completed


def test_update_confirmation_and_missing_mission_copy() -> None:
    updated = {
        "title": "فاتورة WKK",
        "next_step": "انتظر الرد",
        "_operation": "next_step",
    }
    assert "انتظر الرد" in mission_created_message("ar", updated)
    assert "ما عندك مهمة مفتوحة" in mission_created_message("ar", {"_operation": "missing"})
