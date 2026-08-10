from datetime import datetime, timezone

import pytest

from appointment_contract import (
    AppointmentFacts,
    AppointmentState,
    apply_user_correction,
    initial_state,
    privacy_safe_event_names,
    require_transition,
    verified_plan,
)

LANGUAGES = ("de", "ar", "en", "uk", "el")
WHEN = datetime(2026, 9, 15, 9, 30, tzinfo=timezone.utc)

@pytest.mark.parametrize("language", LANGUAGES)
def test_verified_appointment_preserves_exact_facts(language):
    facts = AppointmentFacts(
        conversation_language=language,
        purpose="Termin bei der Ausländerbehörde",
        starts_at=WHEN,
        location_or_channel="Musterstraße 10, Berlin",
        organizer="Ausländerbehörde",
        official_requirements=("Pass", "Terminbestätigung"),
        suggested_preparation=("10 Minuten früher da sein",),
        reminder_requested=True,
    )
    plan = verified_plan(facts)
    assert plan.starts_at == WHEN
    assert plan.location_or_channel == "Musterstraße 10, Berlin"
    assert plan.official_requirements == ("Pass", "Terminbestätigung")
    assert plan.suggested_preparation == ("10 Minuten früher da sein",)
    assert plan.reminder_eligible is True
    assert plan.booking_claim_allowed is False

@pytest.mark.parametrize("language", LANGUAGES)
def test_missing_time_or_location_requires_clarification(language):
    assert initial_state(AppointmentFacts(language, "Behördentermin", None, "Berlin")) == AppointmentState.NEEDS_CLARIFICATION
    assert initial_state(AppointmentFacts(language, "Behördentermin", WHEN, "")) == AppointmentState.NEEDS_CLARIFICATION

@pytest.mark.parametrize("language", LANGUAGES)
def test_change_request_is_reviewable_draft_not_external_confirmation(language):
    facts = AppointmentFacts(language, "Arztpraxis Verwaltung", WHEN, "Telefon", change_requested="reschedule")
    plan = verified_plan(facts)
    assert plan.change_draft_review_required is True
    assert plan.booking_claim_allowed is False

@pytest.mark.parametrize("language", LANGUAGES)
def test_confirmed_external_fact_may_be_reflected_only_when_supplied(language):
    facts = AppointmentFacts(language, "Service-Termin", WHEN, "Online", appointment_confirmed=True)
    assert verified_plan(facts).booking_claim_allowed is True

@pytest.mark.parametrize("language", LANGUAGES)
def test_high_risk_cases_fail_closed(language):
    facts = AppointmentFacts(language, "urgent", WHEN, "office", risk_category="detention_or_deportation")
    assert initial_state(facts) == AppointmentState.BLOCKED_OR_ESCALATED
    with pytest.raises(ValueError, match="appointment_plan_unavailable"):
        verified_plan(facts)

@pytest.mark.parametrize("language", LANGUAGES)
def test_reminder_requires_request_and_readiness(language):
    base = AppointmentFacts(language, "Termin", WHEN, "office", reminder_requested=True, reminder_ready=False)
    assert verified_plan(base).reminder_eligible is False
    assert verified_plan(apply_user_correction(base, reminder_ready=True)).reminder_eligible is True


def test_state_transitions_are_bounded():
    assert require_transition(AppointmentState.RECEIVED, AppointmentState.UNDERSTOOD) == AppointmentState.UNDERSTOOD
    with pytest.raises(ValueError, match="appointment_transition_invalid"):
        require_transition(AppointmentState.COMPLETED, AppointmentState.RECEIVED)


def test_privacy_events_do_not_contain_user_facts():
    events = privacy_safe_event_names()
    assert "appointment_help_started" in events
    assert all("Musterstraße" not in event and "Ausländerbehörde" not in event for event in events)


def test_unsupported_language_fails_closed():
    with pytest.raises(ValueError, match="appointment_language_unsupported"):
        initial_state(AppointmentFacts("fr", "Termin", WHEN, "office"))
