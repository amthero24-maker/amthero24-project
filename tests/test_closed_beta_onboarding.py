import pytest

from closed_beta_onboarding import (
    BETA_NOTICE_VERSION,
    beta_admitted_message,
    beta_declined_message,
    beta_full_message,
    beta_notice,
    beta_opt_in_decision,
    onboarding_config,
)


def test_admission_is_disabled_by_default_with_wave_one_capacity_five():
    config = onboarding_config({})
    assert config.enabled is False
    assert config.capacity == 5
    assert config.wave == "wave1"
    assert config.tenant_key == "default"
    assert config.notice_version == BETA_NOTICE_VERSION
    assert config.policy.enabled is False
    assert config.policy.capacity == 5


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
def test_explicit_valid_enable_flag_is_accepted(value):
    assert onboarding_config({"CLOSED_BETA_ADMISSION_ENABLED": value}).enabled is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
def test_explicit_valid_disable_flag_is_accepted(value):
    assert onboarding_config({"CLOSED_BETA_ADMISSION_ENABLED": value}).enabled is False


def test_invalid_enable_flag_fails_closed_instead_of_becoming_false_silently():
    with pytest.raises(ValueError, match="closed_beta_admission_enabled_invalid"):
        onboarding_config({"CLOSED_BETA_ADMISSION_ENABLED": "maybe"})


@pytest.mark.parametrize("value", ["0", "101", "five", "-1"])
def test_invalid_capacity_fails_closed(value):
    with pytest.raises(ValueError, match="closed_beta_admission_capacity_invalid"):
        onboarding_config({"CLOSED_BETA_ADMISSION_CAPACITY": value})


@pytest.mark.parametrize(
    "env,code",
    [
        ({"CLOSED_BETA_ADMISSION_WAVE": "Wave 1"}, "closed_beta_admission_wave_invalid"),
        ({"CLOSED_BETA_TENANT_KEY": "tenant/key"}, "closed_beta_tenant_key_invalid"),
        ({"CLOSED_BETA_NOTICE_VERSION": "old-notice"}, "closed_beta_notice_version_invalid"),
    ],
)
def test_invalid_scope_or_notice_version_fails_closed(env, code):
    with pytest.raises(ValueError, match=code):
        onboarding_config(env)


@pytest.mark.parametrize("language", ["de", "ar", "en", "uk", "el"])
def test_notice_and_outcome_messages_exist_for_all_supported_languages(language):
    notice = beta_notice(language)
    assert len(notice) > 80
    assert beta_declined_message(language)
    assert beta_full_message(language)
    assert beta_admitted_message(language)


def test_unknown_language_falls_back_to_german_notice():
    assert beta_notice("fr") == beta_notice("de")


@pytest.mark.parametrize(
    "text",
    ["نعم", "موافق", "ja", "ich möchte teilnehmen", "yes", "join beta", "так", "ναι"],
)
def test_beta_opt_in_accepts_supported_affirmative_phrases(text):
    assert beta_opt_in_decision(text) is True


@pytest.mark.parametrize(
    "text",
    ["لا", "ما بدي شارك", "nein", "nicht teilnehmen", "no", "do not join", "ні", "όχι"],
)
def test_beta_opt_in_accepts_supported_declines(text):
    assert beta_opt_in_decision(text) is False


@pytest.mark.parametrize("text", ["مرحبا", "hallo", "help", "شو بتعمل", "maybe later"])
def test_unrelated_text_is_not_treated_as_beta_consent(text):
    assert beta_opt_in_decision(text) is None


def test_notice_is_separate_from_hero_memory_consent_language():
    serialized = " ".join(beta_notice(language) for language in ("de", "ar", "en", "uk", "el")).casefold()
    assert "memory_consent" not in serialized
    assert "hero memory" not in serialized
