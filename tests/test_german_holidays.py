"""Deterministic coverage for German state-wide public holidays."""
from __future__ import annotations

from datetime import date

import pytest

from german_holidays import (
    GERMAN_STATE_CODES,
    canonical_german_state_code,
    german_state_label,
    german_statewide_public_holidays,
    is_german_statewide_public_holiday,
)


def test_all_sixteen_states_have_stable_codes_and_labels() -> None:
    assert len(GERMAN_STATE_CODES) == 16
    assert canonical_german_state_code(" be ") == "BE"
    assert canonical_german_state_code("XX") == ""
    assert german_state_label("NW", "ar") == "شمال الراين-وستفاليا"


@pytest.mark.parametrize(("region", "day"), (
    ("BE", date(2026, 3, 8)),    # Internationaler Frauentag
    ("MV", date(2026, 3, 8)),
    ("NW", date(2026, 6, 4)),    # Fronleichnam
    ("SL", date(2026, 8, 15)),   # Mariä Himmelfahrt, state-wide
    ("TH", date(2026, 9, 20)),   # Weltkindertag
    ("SH", date(2026, 10, 31)),  # Reformationstag
    ("SN", date(2026, 11, 18)),  # Buß- und Bettag
    ("BB", date(2026, 4, 5)),    # Ostersonntag
))
def test_state_specific_holidays_are_calculated(region: str, day: date) -> None:
    assert is_german_statewide_public_holiday(day, region)


def test_municipal_exceptions_are_not_misrepresented_as_statewide() -> None:
    assert date(2026, 8, 8) not in german_statewide_public_holidays(2026, "BY")
    assert date(2026, 8, 15) not in german_statewide_public_holidays(2026, "BY")
    assert date(2026, 8, 15) in german_statewide_public_holidays(2026, "SL")


def test_nationwide_movable_holidays_match_official_2026_calendar() -> None:
    berlin = german_statewide_public_holidays(2026, "BE")
    assert date(2026, 4, 3) in berlin   # Karfreitag
    assert date(2026, 4, 6) in berlin   # Ostermontag
    assert date(2026, 5, 14) in berlin  # Christi Himmelfahrt
    assert date(2026, 5, 25) in berlin  # Pfingstmontag
    assert date(2026, 6, 4) not in berlin


def test_invalid_region_never_creates_a_holiday_match() -> None:
    assert german_statewide_public_holidays(2026, "XX") == frozenset()
    assert not is_german_statewide_public_holiday(date(2026, 12, 25), "XX")
