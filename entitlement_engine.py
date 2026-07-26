"""Privacy-conscious plans, entitlements, and usage accounting for AmtHero24."""
from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from psycopg.types.json import Jsonb

SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}
SUPPORTED_PLANS = {"free", "beta", "hero", "family", "business"}
SUPPORTED_METRICS = {"messages_daily", "images_monthly", "documents_monthly", "voice_monthly"}
ACTIVE_STATUSES = {"active", "trial", "beta"}


@dataclass(frozen=True)
class PlanDefinition:
    code: str
    limits: dict[str, int | None]


@dataclass(frozen=True)
class EntitlementDecision:
    allowed: bool
    plan_code: str
    metric: str
    used: int
    limit: int | None
    remaining: int | None
    enforcement_enabled: bool
    reason: str


def _flag(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().casefold() in {"1", "true", "yes", "on"}


def enforcement_enabled() -> bool:
    """Return whether quota decisions may block requests."""
    return _flag("ENTITLEMENT_ENFORCEMENT_ENABLED", False)


def _limit(name: str, default: int | None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if raw.casefold() in {"none", "unlimited", "inf", "infinite", "-1"}:
        return None
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def plan_catalog() -> dict[str, PlanDefinition]:
    """Build a runtime-configurable catalog without embedding prices or payment claims."""
    return {
        "free": PlanDefinition("free", {
            "messages_daily": None,
            "images_monthly": _limit("ENTITLEMENT_FREE_IMAGES_MONTHLY", 3),
            "documents_monthly": _limit("ENTITLEMENT_FREE_DOCUMENTS_MONTHLY", 3),
            "voice_monthly": _limit("ENTITLEMENT_FREE_VOICE_MONTHLY", 5),
        }),
        "beta": PlanDefinition("beta", {
            "messages_daily": None,
            "images_monthly": _limit("ENTITLEMENT_BETA_IMAGES_MONTHLY", 50),
            "documents_monthly": _limit("ENTITLEMENT_BETA_DOCUMENTS_MONTHLY", 50),
            "voice_monthly": _limit("ENTITLEMENT_BETA_VOICE_MONTHLY", 100),
        }),
        "hero": PlanDefinition("hero", {
            "messages_daily": None,
            "images_monthly": _limit("ENTITLEMENT_HERO_IMAGES_MONTHLY", 150),
            "documents_monthly": _limit("ENTITLEMENT_HERO_DOCUMENTS_MONTHLY", 150),
            "voice_monthly": _limit("ENTITLEMENT_HERO_VOICE_MONTHLY", 300),
        }),
        "family": PlanDefinition("family", {
            "messages_daily": None,
            "images_monthly": _limit("ENTITLEMENT_FAMILY_IMAGES_MONTHLY", 400),
            "documents_monthly": _limit("ENTITLEMENT_FAMILY_DOCUMENTS_MONTHLY", 400),
            "voice_monthly": _limit("ENTITLEMENT_FAMILY_VOICE_MONTHLY", 800),
        }),
        "business": PlanDefinition("business", {
            "messages_daily": None,
            "images_monthly": _limit("ENTITLEMENT_BUSINESS_IMAGES_MONTHLY", 1500),
            "documents_monthly": _limit("ENTITLEMENT_BUSINESS_DOCUMENTS_MONTHLY", 1500),
            "voice_monthly": _limit("ENTITLEMENT_BUSINESS_VOICE_MONTHLY", 3000),
        }),
    }


def default_plan_code() -> str:
    requested = os.getenv("ENTITLEMENT_DEFAULT_PLAN", "beta").strip().casefold()
    return requested if requested in SUPPORTED_PLANS else "beta"


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in phone if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _period_start(metric: str, current: datetime) -> date:
    if metric.endswith("_daily"):
        return current.date()
    return current.date().replace(day=1)


def _safe_plan(code: str) -> str:
    normalized = str(code or "").casefold().strip()
    return normalized if normalized in SUPPORTED_PLANS else default_plan_code()


class EntitlementRepository:
    """Durable plan assignment and aggregate usage without storing raw phone numbers."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _initialize_postgres_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS hero_entitlements (
                phone_hash TEXT PRIMARY KEY,
                plan_code TEXT NOT NULL DEFAULT 'beta',
                status TEXT NOT NULL DEFAULT 'beta',
                source TEXT NOT NULL DEFAULT 'system',
                valid_until TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS hero_usage_counters (
                phone_hash TEXT NOT NULL,
                metric TEXT NOT NULL,
                period_start DATE NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (phone_hash, metric, period_start)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS hero_entitlements_plan_status_idx
            ON hero_entitlements (plan_code, status)
            """,
            """
            CREATE INDEX IF NOT EXISTS hero_usage_metric_period_idx
            ON hero_usage_counters (metric, period_start)
            """,
        )
        with self.store.pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    def _effective_assignment(self, record: dict[str, Any] | None, current: datetime) -> dict[str, Any]:
        if not record:
            return {
                "plan_code": default_plan_code(),
                "status": "beta" if default_plan_code() == "beta" else "active",
                "source": "default",
                "valid_until": None,
            }
        result = dict(record)
        result["plan_code"] = _safe_plan(str(result.get("plan_code") or ""))
        valid_until = _as_datetime(result.get("valid_until"))
        if valid_until and valid_until <= current:
            result.update({"plan_code": "free", "status": "expired"})
        return result

    def get_assignment(self, phone: str, *, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT plan_code, status, source, valid_until, created_at, updated_at
                    FROM hero_entitlements WHERE phone_hash = %s
                    """,
                    (key,),
                ).fetchone()
            return self._effective_assignment(dict(row) if row else None, current)

        snapshot = self.store.snapshot()
        record = snapshot.get("entitlements", {}).get(key)
        return self._effective_assignment(record if isinstance(record, dict) else None, current)

    def set_plan(
        self,
        phone: str,
        plan_code: str,
        *,
        status: str = "active",
        source: str = "manual",
        valid_until: datetime | None = None,
    ) -> dict[str, Any]:
        plan = _safe_plan(plan_code)
        clean_status = str(status or "active").strip().casefold()[:40]
        clean_source = str(source or "manual").strip()[:80]
        key = _phone_hash(phone)
        valid = _now(valid_until) if valid_until else None
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    INSERT INTO hero_entitlements (phone_hash, plan_code, status, source, valid_until)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (phone_hash) DO UPDATE
                    SET plan_code = EXCLUDED.plan_code,
                        status = EXCLUDED.status,
                        source = EXCLUDED.source,
                        valid_until = EXCLUDED.valid_until,
                        updated_at = NOW()
                    RETURNING plan_code, status, source, valid_until, created_at, updated_at
                    """,
                    (key, plan, clean_status, clean_source, valid),
                ).fetchone()
            return dict(row)

        def save(data: dict[str, Any]) -> dict[str, Any]:
            entitlements = data.setdefault("entitlements", {})
            previous = entitlements.get(key, {}) if isinstance(entitlements.get(key), dict) else {}
            now_iso = _now().isoformat()
            record = {
                "plan_code": plan,
                "status": clean_status,
                "source": clean_source,
                "valid_until": valid.isoformat() if valid else None,
                "created_at": previous.get("created_at") or now_iso,
                "updated_at": now_iso,
            }
            entitlements[key] = record
            return deepcopy(record)

        return self.store._transaction(save)

    def _current_count(self, phone: str, metric: str, current: datetime) -> int:
        key = _phone_hash(phone)
        period = _period_start(metric, current)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    "SELECT count FROM hero_usage_counters WHERE phone_hash = %s AND metric = %s AND period_start = %s",
                    (key, metric, period),
                ).fetchone()
            return int(row["count"] if row else 0)
        snapshot = self.store.snapshot()
        compound = f"{key}:{metric}:{period.isoformat()}"
        record = snapshot.get("usage_counters", {}).get(compound, {})
        return int(record.get("count", 0)) if isinstance(record, dict) else 0

    def check_and_consume(
        self,
        phone: str,
        metric: str,
        *,
        amount: int = 1,
        now: datetime | None = None,
        enforce: bool | None = None,
    ) -> EntitlementDecision:
        if metric not in SUPPORTED_METRICS:
            raise ValueError(f"Unsupported entitlement metric: {metric}")
        increment = max(1, int(amount))
        current = _now(now)
        assignment = self.get_assignment(phone, now=current)
        plan_code = _safe_plan(str(assignment.get("plan_code") or ""))
        limit = plan_catalog()[plan_code].limits.get(metric)
        enforcement = enforcement_enabled() if enforce is None else bool(enforce)
        key = _phone_hash(phone)
        period = _period_start(metric, current)

        if enforcement and limit is not None and increment > limit:
            used = self._current_count(phone, metric, current)
            return EntitlementDecision(False, plan_code, metric, used, limit, max(limit - used, 0), True, "quota_reached")

        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                if enforcement and limit is not None:
                    row = connection.execute(
                        """
                        INSERT INTO hero_usage_counters (phone_hash, metric, period_start, count)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (phone_hash, metric, period_start) DO UPDATE
                        SET count = hero_usage_counters.count + EXCLUDED.count,
                            updated_at = NOW()
                        WHERE hero_usage_counters.count + EXCLUDED.count <= %s
                        RETURNING count
                        """,
                        (key, metric, period, increment, limit),
                    ).fetchone()
                    if not row:
                        used = self._current_count(phone, metric, current)
                        return EntitlementDecision(False, plan_code, metric, used, limit, 0, True, "quota_reached")
                else:
                    row = connection.execute(
                        """
                        INSERT INTO hero_usage_counters (phone_hash, metric, period_start, count)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (phone_hash, metric, period_start) DO UPDATE
                        SET count = hero_usage_counters.count + EXCLUDED.count,
                            updated_at = NOW()
                        RETURNING count
                        """,
                        (key, metric, period, increment),
                    ).fetchone()
                used = int(row["count"])
        else:
            compound = f"{key}:{metric}:{period.isoformat()}"

            def consume(data: dict[str, Any]) -> tuple[bool, int]:
                counters = data.setdefault("usage_counters", {})
                record = counters.setdefault(compound, {
                    "phone_hash": key,
                    "metric": metric,
                    "period_start": period.isoformat(),
                    "count": 0,
                    "updated_at": current.isoformat(),
                })
                used_before = int(record.get("count", 0))
                if enforcement and limit is not None and used_before + increment > limit:
                    return False, used_before
                record["count"] = used_before + increment
                record["updated_at"] = current.isoformat()
                return True, int(record["count"])

            accepted, used = self.store._transaction(consume)
            if not accepted:
                return EntitlementDecision(False, plan_code, metric, used, limit, 0, True, "quota_reached")

        remaining = None if limit is None else max(limit - used, 0)
        reason = "unlimited" if limit is None else ("observe_only" if not enforcement else "allowed")
        return EntitlementDecision(True, plan_code, metric, used, limit, remaining, enforcement, reason)

    def summary(self, phone: str, *, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        assignment = self.get_assignment(phone, now=current)
        plan_code = _safe_plan(str(assignment.get("plan_code") or ""))
        limits = deepcopy(plan_catalog()[plan_code].limits)
        usage = {metric: self._current_count(phone, metric, current) for metric in SUPPORTED_METRICS}
        valid_until = _as_datetime(assignment.get("valid_until"))
        return {
            "plan_code": plan_code,
            "status": str(assignment.get("status") or "active"),
            "valid_until": valid_until.isoformat() if valid_until else None,
            "limits": limits,
            "usage": usage,
            "enforcement_enabled": enforcement_enabled(),
        }

    def export_user(self, phone: str, *, now: datetime | None = None) -> dict[str, Any]:
        summary = self.summary(phone, now=now)
        return {
            "plan": summary["plan_code"],
            "status": summary["status"],
            "valid_until": summary["valid_until"],
            "usage": summary["usage"],
        }

    def delete_user(self, phone: str) -> bool:
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                usage = connection.execute("DELETE FROM hero_usage_counters WHERE phone_hash = %s", (key,))
                entitlement = connection.execute("DELETE FROM hero_entitlements WHERE phone_hash = %s", (key,))
            return bool(max(usage.rowcount, 0) or max(entitlement.rowcount, 0))

        def delete(data: dict[str, Any]) -> bool:
            removed = data.setdefault("entitlements", {}).pop(key, None) is not None
            counters = data.setdefault("usage_counters", {})
            matching = [compound for compound, item in counters.items() if isinstance(item, dict) and item.get("phone_hash") == key]
            for compound in matching:
                counters.pop(compound, None)
            return bool(removed or matching)

        return self.store._transaction(delete)


def plan_label(plan_code: str, language: str) -> str:
    labels = {
        "ar": {"free": "الأساسية", "beta": "التجريبية", "hero": "Hero", "family": "العائلة", "business": "الأعمال"},
        "de": {"free": "Basis", "beta": "Beta", "hero": "Hero", "family": "Familie", "business": "Business"},
        "en": {"free": "Basic", "beta": "Beta", "hero": "Hero", "family": "Family", "business": "Business"},
        "uk": {"free": "Базовий", "beta": "Beta", "hero": "Hero", "family": "Сімейний", "business": "Business"},
        "el": {"free": "Βασικό", "beta": "Beta", "hero": "Hero", "family": "Οικογένεια", "business": "Business"},
    }
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    return labels[lang].get(_safe_plan(plan_code), plan_code)


def _usage_line(language: str, label: str, used: int, limit: int | None) -> str:
    unlimited = {"ar": "مفتوح", "de": "offen", "en": "open", "uk": "без обмеження", "el": "ανοιχτό"}[language]
    value = unlimited if limit is None else f"{used}/{limit}"
    return f"• {label}: {value}"


def plan_summary_message(language: str, summary: dict[str, Any]) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    labels = {
        "ar": {"heading": "خطتك الحالية", "images": "الصور هذا الشهر", "documents": "المستندات هذا الشهر", "voice": "الصوتيات هذا الشهر", "note": "المحادثة النصية الأساسية تبقى متاحة. الأسعار والدفع غير مفعّلين حاليًا."},
        "de": {"heading": "Dein aktueller Zugang", "images": "Bilder diesen Monat", "documents": "Dokumente diesen Monat", "voice": "Sprachnachrichten diesen Monat", "note": "Die grundlegende Texthilfe bleibt verfügbar. Preise und Zahlung sind derzeit nicht aktiviert."},
        "en": {"heading": "Your current access", "images": "Images this month", "documents": "Documents this month", "voice": "Voice notes this month", "note": "Core text help remains available. Pricing and payments are not enabled yet."},
        "uk": {"heading": "Твій поточний доступ", "images": "Зображення цього місяця", "documents": "Документи цього місяця", "voice": "Голосові цього місяця", "note": "Основна текстова допомога залишається доступною. Оплати ще не ввімкнено."},
        "el": {"heading": "Η τρέχουσα πρόσβασή σου", "images": "Εικόνες αυτόν τον μήνα", "documents": "Έγγραφα αυτόν τον μήνα", "voice": "Φωνητικά αυτόν τον μήνα", "note": "Η βασική βοήθεια κειμένου παραμένει διαθέσιμη. Οι πληρωμές δεν έχουν ενεργοποιηθεί ακόμη."},
    }[lang]
    usage = summary.get("usage", {})
    limits = summary.get("limits", {})
    lines = [f"{labels['heading']}: {plan_label(str(summary.get('plan_code') or ''), lang)}"]
    lines.append(_usage_line(lang, labels["images"], int(usage.get("images_monthly", 0)), limits.get("images_monthly")))
    lines.append(_usage_line(lang, labels["documents"], int(usage.get("documents_monthly", 0)), limits.get("documents_monthly")))
    lines.append(_usage_line(lang, labels["voice"], int(usage.get("voice_monthly", 0)), limits.get("voice_monthly")))
    lines.append(labels["note"])
    return "\n".join(lines)


def limit_reached_message(language: str, metric: str) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    feature = {
        "ar": {"images_monthly": "الصور", "documents_monthly": "المستندات", "voice_monthly": "الرسائل الصوتية"},
        "de": {"images_monthly": "Bilder", "documents_monthly": "Dokumente", "voice_monthly": "Sprachnachrichten"},
        "en": {"images_monthly": "images", "documents_monthly": "documents", "voice_monthly": "voice notes"},
        "uk": {"images_monthly": "зображень", "documents_monthly": "документів", "voice_monthly": "голосових"},
        "el": {"images_monthly": "εικόνων", "documents_monthly": "εγγράφων", "voice_monthly": "φωνητικών"},
    }[lang].get(metric, metric)
    return {
        "ar": f"وصلت للحد التجريبي الحالي لاستخدام {feature}. فيك تكمل معي بالنص بشكل طبيعي، وما رح ينقطع الموضوع المفتوح.",
        "de": f"Das aktuelle Beta-Limit für {feature} ist erreicht. Du kannst normal per Text weitermachen; ein offenes Thema wird nicht unterbrochen.",
        "en": f"The current beta limit for {feature} has been reached. You can continue normally by text, and an open case will not be interrupted.",
        "uk": f"Досягнуто поточного beta-ліміту для {feature}. Можна продовжити текстом; відкрита справа не перерветься.",
        "el": f"Έφτασες το τρέχον beta όριο για {feature}. Μπορείς να συνεχίσεις με κείμενο χωρίς να διακοπεί η ανοιχτή υπόθεση.",
    }[lang]
