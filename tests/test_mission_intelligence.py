"""Natural Mission Engine extension tests."""
from datetime import UTC, datetime

from mission_engine import MissionIntent, detect_mission_intent, mission_title
from mission_intelligence import enhanced_detect_mission_intent, enhanced_mission_title


def test_generic_arabic_track_command_extracts_real_title() -> None:
    intent = enhanced_detect_mission_intent("تابعلي فاتورة WKK", detect_mission_intent)
    assert intent == MissionIntent("create", "فاتورة WKK")


def test_relative_deadline_tomorrow_is_deterministic() -> None:
    now = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
    intent = enhanced_detect_mission_intent("الموعد بكرا", detect_mission_intent, now=now)
    assert intent == MissionIntent("create", "@mission-due:2026-07-27")


def test_relative_deadline_after_days_is_bounded() -> None:
    now = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
    intent = enhanced_detect_mission_intent("ذكرني بعد 3 أيام", detect_mission_intent, now=now)
    assert intent == MissionIntent("create", "@mission-due:2026-07-29")


def test_technical_topic_becomes_human_mission_title() -> None:
    intent = MissionIntent("create")
    title = enhanced_mission_title(
        intent,
        current_topic="invoice",
        last_message="بدي تابع هالموضوع",
        original_title=mission_title,
    )
    assert title == "متابعة الفاتورة أو الدفعة"
