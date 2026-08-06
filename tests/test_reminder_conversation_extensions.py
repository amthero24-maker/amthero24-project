"""Acceptance tests for concise conversational reminders."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import reminder_conversation_extensions as reminders
from data_store import JsonDataStore

STRONG_REMINDER_KEY = "reminder-key-2026-unique-7fA9xQ2mLp8V"


def _seed_user(store: JsonDataStore, *, topic: str = "identity") -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "ar",
        "current_topic": topic,
    })


def test_after_one_minute_is_exact_even_during_quiet_hours() -> None:
    now = datetime(2026, 8, 5, 1, 52, tzinfo=UTC)
    intent = reminders.detect_conversational_reminder_intent(
        "ذكرني بعد دقيقة اشرب مي", now=now, timezone_name="UTC"
    )
    assert intent is not None
    assert intent.title == "اشرب مي"
    assert intent.exact_time is True
    scheduled = reminders.resolve_conversational_schedule(intent, None, now=now, timezone_name="UTC")
    assert scheduled == now + timedelta(minutes=1)


def test_relative_minutes_and_hours_need_no_full_date() -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    two_minutes = reminders.detect_conversational_reminder_intent("ذكرني بعد دقيقتين اتصل بالمكتب", now=now, timezone_name="UTC")
    ninety_minutes = reminders.detect_conversational_reminder_intent("ذكرني بعد 90 دقيقة ابعت الورقة", now=now, timezone_name="UTC")
    two_hours = reminders.detect_conversational_reminder_intent("ذكرني بعد ساعتين راجع الايميل", now=now, timezone_name="UTC")
    assert two_minutes and two_minutes.scheduled_at == now + timedelta(minutes=2)
    assert ninety_minutes and ninety_minutes.scheduled_at == now + timedelta(minutes=90)
    assert two_hours and two_hours.scheduled_at == now + timedelta(hours=2)


def test_arabic_word_hours_are_understood_and_removed_from_title() -> None:
    now = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)
    four_hours = reminders.detect_conversational_reminder_intent(
        "ذكرني بعد اربع ساعات اشرب مي", now=now, timezone_name="UTC"
    )
    one_hour = reminders.detect_conversational_reminder_intent(
        "ذكرني بعد ساعة اتصل بالمكتب", now=now, timezone_name="UTC"
    )
    assert four_hours and four_hours.scheduled_at == now + timedelta(hours=4)
    assert four_hours.title == "اشرب مي"
    assert one_hour and one_hour.scheduled_at == now + timedelta(hours=1)
    assert one_hour.title == "اتصل بالمكتب"


def test_explicit_same_day_clock_is_understood() -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    intent = reminders.detect_conversational_reminder_intent("ذكرني اليوم الساعة 7 مساء اتصل بامي", now=now, timezone_name="UTC")
    assert intent is not None
    assert intent.scheduled_at == datetime(2026, 8, 5, 19, 0, tzinfo=UTC)
    assert intent.title == "اتصل بامي"


def test_reschedule_understands_position_and_relative_minutes() -> None:
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    intent = reminders.detect_conversational_reminder_intent(
        "أجّل التذكير 2 لمدة 10 دقائق", now=now, timezone_name="UTC"
    )
    assert intent is not None
    assert intent.action == "reschedule"
    assert intent.position == 2
    assert intent.scheduled_at == now + timedelta(minutes=10)


def test_cancel_understands_multiple_arabic_ordinal_positions() -> None:
    intent = reminders.detect_conversational_reminder_intent(
        "الغي التذكير الاول والثاني",
        now=datetime(2026, 8, 6, 11, 32, tzinfo=UTC),
        timezone_name="UTC",
    )
    assert intent is not None
    assert intent.action == "cancel"
    assert intent.positions == (1, 2)


def test_daily_recurrence_is_bounded_and_removed_from_title() -> None:
    intent = reminders.detect_conversational_reminder_intent(
        "ذكرني كل يوم الساعة 8 اشرب الدواء لمدة 7 أيام",
        now=datetime(2026, 8, 6, 5, tzinfo=UTC), timezone_name="UTC",
    )
    assert intent is not None
    assert intent.recurrence_days == 1
    assert intent.recurrence_count == 7
    assert intent.title == "اشرب الدواء"


def test_unbounded_recurrence_asks_for_count() -> None:
    intent = reminders.detect_conversational_reminder_intent(
        "ذكرني كل يوم الساعة 8 اشرب الدواء",
        now=datetime(2026, 8, 6, 5, tzinfo=UTC), timezone_name="UTC",
    )
    assert intent is not None
    assert intent.recurrence_days == 1
    assert intent.recurrence_count is None


@pytest.mark.parametrize(("text", "days", "count", "title"), (
    ("remind me every day tomorrow drink water for 5 days", 1, 5, "drink water"),
    ("erinnere mich jede woche morgen um 9 Unterlagen prüfen für 4 wochen", 7, 4, "Unterlagen prüfen"),
    ("нагадай мені щодня завтра пити воду протягом 5 днів", 1, 5, "пити воду"),
    ("θυμισε μου καθε εβδομαδα αυριο ελεγξε τα εγγραφα για 4 εβδομαδες", 7, 4, "ελεγξε τα εγγραφα"),
))
def test_multilingual_bounded_recurrence(text, days, count, title) -> None:
    intent = reminders.detect_conversational_reminder_intent(
        text, now=datetime(2026, 8, 6, 5, tzinfo=UTC), timezone_name="UTC"
    )
    assert intent is not None
    assert intent.recurrence_days == days
    assert intent.recurrence_count == count
    assert intent.title == title


def test_conversation_topics_are_never_reminder_titles() -> None:
    assert reminders._real_mission_title({"title": "identity"}) == ""
    assert reminders._real_mission_title({"title": "greeting_3"}) == ""
    assert reminders._real_mission_title({"title": "موعد الإقامة"}) == "موعد الإقامة"


@pytest.mark.anyio
async def test_missing_subject_asks_once_then_creates_from_short_answer(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    store = JsonDataStore(tmp_path / "store.json")
    reminders.core.store = store
    reminders.core._hero_memory_store = reminders.core.HeroMemory(store)
    reminders.base._REMINDER_REPOSITORY = None
    _seed_user(store)

    first = reminders.core.IncomingMessage("r1", "49123", "ذكرني بعد دقيقة", "text")
    store.claim_message(first.message_id, first.sender, first.text)
    with patch.object(reminders.base, "reminder_delivery_ready", return_value=True), patch.object(
        reminders.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await reminders.process_incoming(first)
        assert "شو بتحب ذكّرك فيه" in send.await_args.args[1]

        second = reminders.core.IncomingMessage("r2", "49123", "اشرب مي", "text")
        store.claim_message(second.message_id, second.sender, second.text)
        await reminders.process_incoming(second)
        assert "رح ذكّرك" in send.await_args.args[1]

    created = reminders.base._repository().list("49123")
    assert len(created) == 1
    assert created[0]["title"] == "اشرب مي"
    assert "identity" not in created[0]["title"]


@pytest.mark.anyio
async def test_missing_time_asks_only_for_time(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    store = JsonDataStore(tmp_path / "store.json")
    reminders.core.store = store
    reminders.core._hero_memory_store = reminders.core.HeroMemory(store)
    reminders.base._REMINDER_REPOSITORY = None
    _seed_user(store)

    message = reminders.core.IncomingMessage("r3", "49123", "ذكرني اتصل بالمكتب", "text")
    store.claim_message(message.message_id, message.sender, message.text)
    with patch.object(reminders.base, "reminder_delivery_ready", return_value=True), patch.object(
        reminders.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await reminders.process_incoming(message)
    assert "إيمتى أذكّرك" in send.await_args.args[1]
    assert store.get_user("49123")["pending_reminder_title"] == "اتصل بالمكتب"


@pytest.mark.anyio
async def test_existing_time_followup_accepts_arabic_word_hours(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    store = JsonDataStore(tmp_path / "store.json")
    reminders.core.store = store
    reminders.core._hero_memory_store = reminders.core.HeroMemory(store)
    reminders.base._REMINDER_REPOSITORY = None
    _seed_user(store)
    store.update_user("49123", {
        "pending_reminder_title": "اشرب مي",
        "session_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    })

    message = reminders.core.IncomingMessage("r-word-hours", "49123", "بعد اربع ساعات", "text")
    store.claim_message(message.message_id, message.sender, message.text)
    with patch.object(reminders.base, "reminder_delivery_ready", return_value=True), patch.object(
        reminders.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await reminders.process_incoming(message)

    assert "رح ذكّرك" in send.await_args.args[1]
    created = reminders.base._repository().list("49123")
    assert len(created) == 1
    assert created[0]["title"] == "اشرب مي"


@pytest.mark.anyio
async def test_worker_unavailable_does_not_save_conversational_reminder(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_WORKER_ENABLED", "false")
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    store = JsonDataStore(tmp_path / "store.json")
    reminders.core.store = store
    reminders.core._hero_memory_store = reminders.core.HeroMemory(store)
    reminders.base._REMINDER_REPOSITORY = None
    _seed_user(store)
    store.update_user("49123", {
        "pending_reminder_title": "قديم",
        "session_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    })

    message = reminders.core.IncomingMessage("r4", "49123", "ذكرني بعد دقيقة اشرب مي", "text")
    store.claim_message(message.message_id, message.sender, message.text)
    with patch.object(reminders.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await reminders.process_incoming(message)

    assert "متوقفة مؤقتًا" in send.await_args.args[1]
    assert reminders.base._repository().list("49123") == []
    assert "pending_reminder_title" not in store.get_user("49123")


@pytest.mark.anyio
async def test_reschedule_never_guesses_when_multiple_reminders_exist(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    store = JsonDataStore(tmp_path / "store.json")
    reminders.core.store = store
    reminders.core._hero_memory_store = reminders.core.HeroMemory(store)
    reminders.base._REMINDER_REPOSITORY = None
    _seed_user(store)
    repository = reminders.base._repository()
    now = datetime.now(UTC)
    repository.create("49123", title="الأول", scheduled_at=now + timedelta(hours=1), language="ar")
    repository.create("49123", title="الثاني", scheduled_at=now + timedelta(hours=2), language="ar")

    ambiguous = reminders.core.IncomingMessage("r5", "49123", "أجّل التذكير لمدة 10 دقائق", "text")
    store.claim_message(ambiguous.message_id, ambiguous.sender, ambiguous.text)
    with patch.object(reminders.base, "reminder_delivery_ready", return_value=True), patch.object(
        reminders.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await reminders.process_incoming(ambiguous)
        assert "أكثر من تذكير" in send.await_args.args[1]

        selected = reminders.core.IncomingMessage("r6", "49123", "أجّل التذكير 2 لمدة 10 دقائق", "text")
        store.claim_message(selected.message_id, selected.sender, selected.text)
        await reminders.process_incoming(selected)
        assert "أجّلت «الثاني»" in send.await_args.args[1]

    active = repository.list("49123")
    assert active[0]["title"] == "الثاني"
    assert active[1]["title"] == "الأول"


@pytest.mark.anyio
async def test_cancel_multiple_positions_and_never_guess(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    store = JsonDataStore(tmp_path / "store.json")
    reminders.core.store = store
    reminders.core._hero_memory_store = reminders.core.HeroMemory(store)
    reminders.base._REMINDER_REPOSITORY = None
    _seed_user(store)
    repository = reminders.base._repository()
    now = datetime.now(UTC)
    repository.create("49123", title="الأول", scheduled_at=now + timedelta(hours=1), language="ar")
    repository.create("49123", title="الثاني", scheduled_at=now + timedelta(hours=2), language="ar")

    generic = reminders.core.IncomingMessage("r7", "49123", "الغي التذكير", "text")
    store.claim_message(generic.message_id, generic.sender, generic.text)
    with patch.object(reminders.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await reminders.process_incoming(generic)
        assert "حدّد الرقم أو الأرقام" in send.await_args.args[1]
        assert len(repository.list("49123")) == 2

        selected = reminders.core.IncomingMessage("r8", "49123", "الغي التذكير الاول والثاني", "text")
        store.claim_message(selected.message_id, selected.sender, selected.text)
        await reminders.process_incoming(selected)
        assert "ألغيت 2 تذكير" in send.await_args.args[1]

    assert repository.list("49123") == []


@pytest.mark.anyio
async def test_conversation_creates_only_bounded_recurrence(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", STRONG_REMINDER_KEY)
    store = JsonDataStore(tmp_path / "store.json")
    reminders.core.store = store
    reminders.core._hero_memory_store = reminders.core.HeroMemory(store)
    reminders.base._REMINDER_REPOSITORY = None
    _seed_user(store)

    unbounded = reminders.core.IncomingMessage(
        "r9", "49123", "ذكرني كل يوم الساعة 8 اشرب الدواء", "text"
    )
    store.claim_message(unbounded.message_id, unbounded.sender, unbounded.text)
    with patch.object(reminders.base, "reminder_delivery_ready", return_value=True), patch.object(
        reminders.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await reminders.process_incoming(unbounded)
        assert "لكم مرة" in send.await_args.args[1]
        assert reminders.base._repository().list("49123") == []
        profile = store.get_user("49123")
        assert profile["pending_reminder_title"] == "اشرب الدواء"
        assert profile["pending_reminder_recurrence_days"] == "1"

        invalid = reminders.core.IncomingMessage("r9-invalid", "49123", "نعم سبع", "text")
        store.claim_message(invalid.message_id, invalid.sender, invalid.text)
        await reminders.process_incoming(invalid)
        assert "لكم مرة" in send.await_args.args[1]
        assert reminders.base._repository().list("49123") == []

        bounded = reminders.core.IncomingMessage("r10", "49123", "7 أيام", "text")
        store.claim_message(bounded.message_id, bounded.sender, bounded.text)
        await reminders.process_incoming(bounded)
        assert "يتكرر كل 1 يوم، 7 مرات" in send.await_args.args[1]

    created = reminders.base._repository().list("49123")
    assert len(created) == 1
    assert created[0]["title"] == "اشرب الدواء"
    assert created[0]["recurrence_days"] == 1
    assert created[0]["recurrence_remaining"] == 7
    assert "pending_reminder_recurrence_days" not in store.get_user("49123")


def test_recurrence_count_reply_is_strict_and_bounded() -> None:
    assert reminders._parse_recurrence_count_reply("7 أيام", 1) == 7
    assert reminders._parse_recurrence_count_reply("4 أسابيع", 7) == 4
    assert reminders._parse_recurrence_count_reply("1", 1) is None
    assert reminders._parse_recurrence_count_reply("500 أيام", 1) is None
    assert reminders._parse_recurrence_count_reply("نعم 7 أيام", 1) is None


def test_recurrence_control_intents_include_target_and_bounds() -> None:
    update = reminders.detect_conversational_reminder_intent(
        "خلي التذكير 2 كل اسبوع لمدة 4 اسابيع"
    )
    assert update is not None
    assert update.action == "recurrence_update"
    assert update.position == 2
    assert update.recurrence_days == 7
    assert update.recurrence_count == 4

    stop = reminders.detect_conversational_reminder_intent("وقف تكرار التذكير 2")
    assert stop is not None
    assert stop.action == "recurrence_update"
    assert stop.position == 2
    assert stop.recurrence_stop is True
