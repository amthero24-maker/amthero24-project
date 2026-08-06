"""Deterministic German state-wide public-holiday calendar.

The reminder feature deliberately accepts an explicit Bundesland code and never
infers it from a phone number, language, timezone, profile, or document.  The
calendar contains holidays that apply throughout the selected state.  Municipal
exceptions (for example Augsburg's Friedensfest and municipality-dependent
Mariä Himmelfahrt in Bavaria) are intentionally outside this state-only scope.

Primary legal references for the exceptional scope include the Bavarian
Feiertagsgesetz, Art. 1, and the holiday calendars published by state authorities.
The movable dates use the Gregorian computus and do not require a network call.
"""
from __future__ import annotations

from functools import lru_cache
from datetime import date, timedelta


GERMAN_STATE_CODES = frozenset({
    "BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV",
    "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH",
})

GERMAN_STATE_LABELS = {
    "BW": {
        "de": "Baden-Württemberg", "en": "Baden-Württemberg",
        "ar": "بادن-فورتمبيرغ", "uk": "Баден-Вюртемберг", "el": "Βάδη-Βυρτεμβέργη",
    },
    "BY": {
        "de": "Bayern", "en": "Bavaria", "ar": "بافاريا",
        "uk": "Баварія", "el": "Βαυαρία",
    },
    "BE": {
        "de": "Berlin", "en": "Berlin", "ar": "برلين",
        "uk": "Берлін", "el": "Βερολίνο",
    },
    "BB": {
        "de": "Brandenburg", "en": "Brandenburg", "ar": "براندنبورغ",
        "uk": "Бранденбург", "el": "Βρανδεμβούργο",
    },
    "HB": {
        "de": "Bremen", "en": "Bremen", "ar": "بريمن",
        "uk": "Бремен", "el": "Βρέμη",
    },
    "HH": {
        "de": "Hamburg", "en": "Hamburg", "ar": "هامبورغ",
        "uk": "Гамбург", "el": "Αμβούργο",
    },
    "HE": {
        "de": "Hessen", "en": "Hesse", "ar": "هيسن",
        "uk": "Гессен", "el": "Έσση",
    },
    "MV": {
        "de": "Mecklenburg-Vorpommern", "en": "Mecklenburg-Vorpommern",
        "ar": "مكلنبورغ-فوربومرن", "uk": "Мекленбург-Передня Померанія",
        "el": "Μεκλεμβούργο-Δυτική Πομερανία",
    },
    "NI": {
        "de": "Niedersachsen", "en": "Lower Saxony", "ar": "ساكسونيا السفلى",
        "uk": "Нижня Саксонія", "el": "Κάτω Σαξονία",
    },
    "NW": {
        "de": "Nordrhein-Westfalen", "en": "North Rhine-Westphalia",
        "ar": "شمال الراين-وستفاليا", "uk": "Північний Рейн-Вестфалія",
        "el": "Βόρεια Ρηνανία-Βεστφαλία",
    },
    "RP": {
        "de": "Rheinland-Pfalz", "en": "Rhineland-Palatinate",
        "ar": "راينلاند-بالاتينات", "uk": "Рейнланд-Пфальц",
        "el": "Ρηνανία-Παλατινάτο",
    },
    "SL": {
        "de": "Saarland", "en": "Saarland", "ar": "سارلاند",
        "uk": "Саар", "el": "Σάαρλαντ",
    },
    "SN": {
        "de": "Sachsen", "en": "Saxony", "ar": "ساكسونيا",
        "uk": "Саксонія", "el": "Σαξονία",
    },
    "ST": {
        "de": "Sachsen-Anhalt", "en": "Saxony-Anhalt", "ar": "ساكسونيا-أنهالت",
        "uk": "Саксонія-Ангальт", "el": "Σαξονία-Άνχαλτ",
    },
    "SH": {
        "de": "Schleswig-Holstein", "en": "Schleswig-Holstein",
        "ar": "شليسفيغ-هولشتاين", "uk": "Шлезвіг-Гольштейн",
        "el": "Σλέσβιχ-Χόλσταϊν",
    },
    "TH": {
        "de": "Thüringen", "en": "Thuringia", "ar": "تورينغن",
        "uk": "Тюрингія", "el": "Θουριγγία",
    },
}


def canonical_german_state_code(value: object) -> str:
    """Return a supported state code or an empty string for invalid input."""
    code = str(value or "").strip().upper()
    return code if code in GERMAN_STATE_CODES else ""


def german_state_label(code: object, language: str = "de") -> str:
    canonical = canonical_german_state_code(code)
    labels = GERMAN_STATE_LABELS.get(canonical, {})
    return str(labels.get(language) or labels.get("de") or canonical)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month = (h + length - 7 * m + 114) // 31
    day = ((h + length - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _repentance_day(year: int) -> date:
    """Wednesday strictly before 23 November (Buß- und Bettag)."""
    november_23 = date(year, 11, 23)
    days_back = (november_23.weekday() - 2) % 7 or 7
    return november_23 - timedelta(days=days_back)


@lru_cache(maxsize=512)
def german_statewide_public_holidays(year: int, state_code: str) -> frozenset[date]:
    """Return holidays applying throughout ``state_code`` for ``year``.

    Invalid regions fail closed by returning an empty set.  Callers that persist a
    reminder must validate the region before scheduling it.
    """
    region = canonical_german_state_code(state_code)
    if not region or not 1900 <= int(year) <= 2199:
        return frozenset()

    easter = _easter_sunday(int(year))
    holidays = {
        date(year, 1, 1),                 # Neujahr
        easter - timedelta(days=2),       # Karfreitag
        easter + timedelta(days=1),       # Ostermontag
        date(year, 5, 1),                 # Tag der Arbeit
        easter + timedelta(days=39),      # Christi Himmelfahrt
        easter + timedelta(days=50),      # Pfingstmontag
        date(year, 10, 3),                # Tag der Deutschen Einheit
        date(year, 12, 25),
        date(year, 12, 26),
    }

    if region in {"BW", "BY", "ST"}:
        holidays.add(date(year, 1, 6))  # Heilige Drei Könige
    if (region == "BE" and year >= 2019) or (region == "MV" and year >= 2023):
        holidays.add(date(year, 3, 8))  # Internationaler Frauentag
    if region == "BB":
        holidays.update({easter, easter + timedelta(days=49)})
    if region in {"BW", "BY", "HE", "NW", "RP", "SL"}:
        holidays.add(easter + timedelta(days=60))  # Fronleichnam, state-wide scope
    if region == "SL":
        holidays.add(date(year, 8, 15))  # Mariä Himmelfahrt state-wide in Saarland
    if region == "TH" and year >= 2019:
        holidays.add(date(year, 9, 20))  # Weltkindertag
    if region in {"BB", "MV", "SN", "ST", "TH"} or (
        region in {"HB", "HH", "NI", "SH"} and year >= 2018
    ):
        holidays.add(date(year, 10, 31))  # Reformationstag
    if region in {"BW", "BY", "NW", "RP", "SL"}:
        holidays.add(date(year, 11, 1))  # Allerheiligen
    if region == "SN":
        holidays.add(_repentance_day(year))

    # Nationwide one-off 500th Reformation anniversary.
    if year == 2017:
        holidays.add(date(2017, 10, 31))
    # Berlin's state-wide one-off liberation anniversaries.
    if region == "BE" and year in {2020, 2025}:
        holidays.add(date(year, 5, 8))

    return frozenset(holidays)


def is_german_statewide_public_holiday(day: date, state_code: object) -> bool:
    region = canonical_german_state_code(state_code)
    return bool(region and day in german_statewide_public_holidays(day.year, region))
