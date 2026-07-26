"""Mission command parser tests."""
from mission_engine import (
    detect_mission_intent,
    mission_completed_message,
    mission_list_message,
    mission_title,
)


def test_detect_arabic_mission_actions() -> None:
    create = detect_mission_intent("تابعلي هالموضوع فاتورة WKK")
    assert create is not None and create.action == "create"
    assert "فاتورة" in create.title
    assert detect_mission_intent("شو مهامي؟").action == "list"
    assert detect_mission_intent("خلصت المهمة").action == "complete"


def test_mission_title_uses_topic_or_previous_message() -> None:
    intent = detect_mission_intent("تابعلي هالموضوع")
    assert intent is not None
    assert mission_title(intent, current_topic="invoice") == "invoice"
    assert mission_title(intent, last_message="رسالة من WKK") == "رسالة من WKK"


def test_localized_mission_replies_are_whatsapp_friendly() -> None:
    missions = [{"title": "فاتورة WKK", "status": "open"}]
    listed = mission_list_message("ar", missions)
    assert "مهامك المفتوحة" in listed
    assert "فاتورة WKK" in listed
    completed = mission_completed_message("ar", missions[0])
    assert "مكتملة" in completed
