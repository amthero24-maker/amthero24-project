"""Privacy-safe sender rate limits and burst protection for AmtHero24."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class AbuseDecision:
    allowed: bool
    notify: bool
    reason: str
    blocked_until: datetime | None
    minute_count: int
    hour_count: int
    media_hour_count: int


def _flag(name: str, default: bool = True) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().casefold() in {"1", "true", "yes", "on"}


def guard_enabled() -> bool:
    return _flag("ABUSE_GUARD_ENABLED", True)


def enforcement_enabled() -> bool:
    return _flag("ABUSE_GUARD_ENFORCEMENT_ENABLED", True)


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


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
    normalized = "".join(character for character in str(phone or "") if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _floor_minute(current: datetime) -> datetime:
    return current.replace(second=0, microsecond=0)


def _floor_hour(current: datetime) -> datetime:
    return current.replace(minute=0, second=0, microsecond=0)


def _limits() -> tuple[int, int, int]:
    return (
        _int_env("ABUSE_MESSAGES_PER_MINUTE", 20, 5, 600),
        _int_env("ABUSE_MESSAGES_PER_HOUR", 240, 20, 10000),
        _int_env("ABUSE_MEDIA_PER_HOUR", 60, 5, 2000),
    )


def _cooldown(strike_count: int) -> timedelta:
    base = _int_env("ABUSE_COOLDOWN_SECONDS", 60, 15, 3600)
    maximum = _int_env("ABUSE_MAX_COOLDOWN_SECONDS", 1800, base, 86400)
    seconds = min(base * (2 ** max(0, min(strike_count - 1, 6))), maximum)
    return timedelta(seconds=seconds)


class AbuseGuardRepository:
    """Fixed-window guard with escalating cooldowns and no raw sender identifiers."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _initialize_postgres_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS abuse_rate_windows (
                phone_hash TEXT NOT NULL,
                bucket TEXT NOT NULL,
                window_start TIMESTAMPTZ NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (phone_hash, bucket, window_start)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS abuse_blocks (
                phone_hash TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                strike_count INTEGER NOT NULL DEFAULT 1,
                blocked_until TIMESTAMPTZ NOT NULL,
                last_notice_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS abuse_guard_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS abuse_rate_windows_updated_idx ON abuse_rate_windows (updated_at)",
            "CREATE INDEX IF NOT EXISTS abuse_guard_events_created_idx ON abuse_guard_events (created_at)",
        )
        with self.store.pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    def _event(self, event_type: str, reason: str, current: datetime, *, connection: Any | None = None) -> None:
        event = {
            "event_id": uuid4().hex,
            "event_type": str(event_type)[:40],
            "reason": str(reason)[:80],
            "created_at": current.isoformat(),
        }
        if self.backend_name == "postgresql":
            target = connection
            if target is not None:
                target.execute(
                    "INSERT INTO abuse_guard_events (event_id, event_type, reason, created_at) VALUES (%s, %s, %s, %s)",
                    (event["event_id"], event["event_type"], event["reason"], current),
                )
                return
            with self.store.pool.connection() as own_connection:
                own_connection.execute(
                    "INSERT INTO abuse_guard_events (event_id, event_type, reason, created_at) VALUES (%s, %s, %s, %s)",
                    (event["event_id"], event["event_type"], event["reason"], current),
                )
            return

        def append(data: dict[str, Any]) -> None:
            events = data.setdefault("abuse_events", [])
            events.append(event)
            data["abuse_events"] = events[-10000:]

        self.store._transaction(append)

    def _active_json_block(self, data: dict[str, Any], key: str, current: datetime) -> tuple[dict[str, Any] | None, bool]:
        record = data.setdefault("abuse_blocks", {}).get(key)
        if not isinstance(record, dict):
            return None, False
        blocked_until = _as_datetime(record.get("blocked_until"))
        if not blocked_until or blocked_until <= current:
            data["abuse_blocks"].pop(key, None)
            return None, False
        last_notice = _as_datetime(record.get("last_notice_at"))
        notify_every = timedelta(seconds=_int_env("ABUSE_NOTICE_INTERVAL_SECONDS", 60, 30, 3600))
        notify = last_notice is None or current - last_notice >= notify_every
        if notify:
            record["last_notice_at"] = current.isoformat()
            record["updated_at"] = current.isoformat()
        return record, notify

    def check(self, phone: str, *, has_media: bool = False, now: datetime | None = None) -> AbuseDecision:
        current = _now(now)
        if not guard_enabled():
            return AbuseDecision(True, False, "disabled", None, 0, 0, 0)
        key = _phone_hash(phone)
        minute_limit, hour_limit, media_limit = _limits()
        minute_start = _floor_minute(current)
        hour_start = _floor_hour(current)

        if self.backend_name == "postgresql":
            return self._check_postgres(
                key,
                current=current,
                minute_start=minute_start,
                hour_start=hour_start,
                has_media=has_media,
                minute_limit=minute_limit,
                hour_limit=hour_limit,
                media_limit=media_limit,
            )
        return self._check_json(
            key,
            current=current,
            minute_start=minute_start,
            hour_start=hour_start,
            has_media=has_media,
            minute_limit=minute_limit,
            hour_limit=hour_limit,
            media_limit=media_limit,
        )

    def _check_postgres(
        self,
        key: str,
        *,
        current: datetime,
        minute_start: datetime,
        hour_start: datetime,
        has_media: bool,
        minute_limit: int,
        hour_limit: int,
        media_limit: int,
    ) -> AbuseDecision:
        with self.store.pool.connection() as connection:
            block = connection.execute(
                "SELECT reason, strike_count, blocked_until, last_notice_at FROM abuse_blocks WHERE phone_hash = %s FOR UPDATE",
                (key,),
            ).fetchone()
            if block and _as_datetime(block["blocked_until"]) and _as_datetime(block["blocked_until"]) > current:
                last_notice = _as_datetime(block.get("last_notice_at"))
                notice_interval = timedelta(seconds=_int_env("ABUSE_NOTICE_INTERVAL_SECONDS", 60, 30, 3600))
                notify = last_notice is None or current - last_notice >= notice_interval
                if notify:
                    connection.execute(
                        "UPDATE abuse_blocks SET last_notice_at = %s, updated_at = NOW() WHERE phone_hash = %s",
                        (current, key),
                    )
                self._event("blocked_request", str(block["reason"]), current, connection=connection)
                return AbuseDecision(False, notify, str(block["reason"]), _as_datetime(block["blocked_until"]), 0, 0, 0)
            if block:
                connection.execute("DELETE FROM abuse_blocks WHERE phone_hash = %s", (key,))

            def increment(bucket: str, start: datetime) -> int:
                row = connection.execute(
                    """
                    INSERT INTO abuse_rate_windows (phone_hash, bucket, window_start, count)
                    VALUES (%s, %s, %s, 1)
                    ON CONFLICT (phone_hash, bucket, window_start) DO UPDATE
                    SET count = abuse_rate_windows.count + 1, updated_at = NOW()
                    RETURNING count
                    """,
                    (key, bucket, start),
                ).fetchone()
                return int(row["count"])

            minute_count = increment("messages_minute", minute_start)
            hour_count = increment("messages_hour", hour_start)
            media_count = increment("media_hour", hour_start) if has_media else 0
            reason = ""
            if minute_count > minute_limit:
                reason = "minute_burst"
            elif hour_count > hour_limit:
                reason = "hour_volume"
            elif has_media and media_count > media_limit:
                reason = "media_volume"

            if reason and enforcement_enabled():
                previous = connection.execute(
                    "SELECT strike_count FROM abuse_blocks WHERE phone_hash = %s",
                    (key,),
                ).fetchone()
                strikes = int(previous["strike_count"] if previous else 0) + 1
                blocked_until = current + _cooldown(strikes)
                connection.execute(
                    """
                    INSERT INTO abuse_blocks (phone_hash, reason, strike_count, blocked_until, last_notice_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (phone_hash) DO UPDATE
                    SET reason = EXCLUDED.reason,
                        strike_count = abuse_blocks.strike_count + 1,
                        blocked_until = EXCLUDED.blocked_until,
                        last_notice_at = EXCLUDED.last_notice_at,
                        updated_at = NOW()
                    """,
                    (key, reason, strikes, blocked_until, current),
                )
                self._event("block_started", reason, current, connection=connection)
                return AbuseDecision(False, True, reason, blocked_until, minute_count, hour_count, media_count)

            if reason:
                self._event("limit_observed", reason, current, connection=connection)
            return AbuseDecision(True, False, reason or "allowed", None, minute_count, hour_count, media_count)

    def _check_json(
        self,
        key: str,
        *,
        current: datetime,
        minute_start: datetime,
        hour_start: datetime,
        has_media: bool,
        minute_limit: int,
        hour_limit: int,
        media_limit: int,
    ) -> AbuseDecision:
        def transaction(data: dict[str, Any]) -> AbuseDecision:
            active, notify = self._active_json_block(data, key, current)
            if active:
                events = data.setdefault("abuse_events", [])
                events.append({"event_id": uuid4().hex, "event_type": "blocked_request", "reason": active.get("reason", "blocked"), "created_at": current.isoformat()})
                data["abuse_events"] = events[-10000:]
                return AbuseDecision(False, notify, str(active.get("reason") or "blocked"), _as_datetime(active.get("blocked_until")), 0, 0, 0)

            windows = data.setdefault("abuse_windows", {})

            def increment(bucket: str, start: datetime) -> int:
                compound = f"{key}:{bucket}:{start.isoformat()}"
                record = windows.setdefault(compound, {
                    "phone_hash": key,
                    "bucket": bucket,
                    "window_start": start.isoformat(),
                    "count": 0,
                    "updated_at": current.isoformat(),
                })
                record["count"] = int(record.get("count", 0)) + 1
                record["updated_at"] = current.isoformat()
                return int(record["count"])

            minute_count = increment("messages_minute", minute_start)
            hour_count = increment("messages_hour", hour_start)
            media_count = increment("media_hour", hour_start) if has_media else 0
            reason = ""
            if minute_count > minute_limit:
                reason = "minute_burst"
            elif hour_count > hour_limit:
                reason = "hour_volume"
            elif has_media and media_count > media_limit:
                reason = "media_volume"

            events = data.setdefault("abuse_events", [])
            if reason and enforcement_enabled():
                previous = data.setdefault("abuse_blocks", {}).get(key, {})
                strikes = int(previous.get("strike_count", 0)) + 1 if isinstance(previous, dict) else 1
                blocked_until = current + _cooldown(strikes)
                data["abuse_blocks"][key] = {
                    "phone_hash": key,
                    "reason": reason,
                    "strike_count": strikes,
                    "blocked_until": blocked_until.isoformat(),
                    "last_notice_at": current.isoformat(),
                    "updated_at": current.isoformat(),
                }
                events.append({"event_id": uuid4().hex, "event_type": "block_started", "reason": reason, "created_at": current.isoformat()})
                data["abuse_events"] = events[-10000:]
                return AbuseDecision(False, True, reason, blocked_until, minute_count, hour_count, media_count)
            if reason:
                events.append({"event_id": uuid4().hex, "event_type": "limit_observed", "reason": reason, "created_at": current.isoformat()})
                data["abuse_events"] = events[-10000:]
            return AbuseDecision(True, False, reason or "allowed", None, minute_count, hour_count, media_count)

        return self.store._transaction(transaction)

    def delete_user(self, phone: str) -> bool:
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                windows = connection.execute("DELETE FROM abuse_rate_windows WHERE phone_hash = %s", (key,))
                blocks = connection.execute("DELETE FROM abuse_blocks WHERE phone_hash = %s", (key,))
            return bool(max(windows.rowcount, 0) or max(blocks.rowcount, 0))

        def delete(data: dict[str, Any]) -> bool:
            windows = data.setdefault("abuse_windows", {})
            matching = [compound for compound, record in windows.items() if isinstance(record, dict) and record.get("phone_hash") == key]
            for compound in matching:
                windows.pop(compound, None)
            block = data.setdefault("abuse_blocks", {}).pop(key, None)
            return bool(matching or block)

        return self.store._transaction(delete)

    def cleanup(self, *, now: datetime | None = None, retention_days: int = 30) -> dict[str, int]:
        current = _now(now)
        window_cutoff = current - timedelta(hours=2)
        event_cutoff = current - timedelta(days=max(1, retention_days))
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                windows = connection.execute("DELETE FROM abuse_rate_windows WHERE updated_at < %s", (window_cutoff,))
                blocks = connection.execute("DELETE FROM abuse_blocks WHERE blocked_until < %s", (current - timedelta(days=1),))
                events = connection.execute("DELETE FROM abuse_guard_events WHERE created_at < %s", (event_cutoff,))
            return {"windows": max(windows.rowcount, 0), "blocks": max(blocks.rowcount, 0), "events": max(events.rowcount, 0)}

        def clean(data: dict[str, Any]) -> dict[str, int]:
            windows = data.setdefault("abuse_windows", {})
            old_windows = [key for key, record in windows.items() if isinstance(record, dict) and (_as_datetime(record.get("updated_at")) or current) < window_cutoff]
            for key in old_windows:
                windows.pop(key, None)
            blocks = data.setdefault("abuse_blocks", {})
            old_blocks = [key for key, record in blocks.items() if isinstance(record, dict) and (_as_datetime(record.get("blocked_until")) or current) < current - timedelta(days=1)]
            for key in old_blocks:
                blocks.pop(key, None)
            events = data.setdefault("abuse_events", [])
            kept = [event for event in events if isinstance(event, dict) and (_as_datetime(event.get("created_at")) or current) >= event_cutoff]
            removed_events = len(events) - len(kept)
            data["abuse_events"] = kept
            return {"windows": len(old_windows), "blocks": len(old_blocks), "events": removed_events}

        return self.store._transaction(clean)

    def aggregate(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        cutoff = current - timedelta(hours=24)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    "SELECT event_type, reason, COUNT(*) AS count FROM abuse_guard_events WHERE created_at >= %s GROUP BY event_type, reason",
                    (cutoff,),
                ).fetchall()
                active = connection.execute("SELECT COUNT(*) AS count FROM abuse_blocks WHERE blocked_until > %s", (current,)).fetchone()
            by_event: dict[str, int] = {}
            by_reason: dict[str, int] = {}
            for row in rows:
                by_event[str(row["event_type"])] = by_event.get(str(row["event_type"]), 0) + int(row["count"])
                if row["reason"]:
                    by_reason[str(row["reason"])] = by_reason.get(str(row["reason"]), 0) + int(row["count"])
            return {"active_blocks": int(active["count"] if active else 0), "events_24h": dict(sorted(by_event.items())), "reasons_24h": dict(sorted(by_reason.items()))}

        snapshot = self.store.snapshot()
        events = [event for event in snapshot.get("abuse_events", []) if isinstance(event, dict) and (_as_datetime(event.get("created_at")) or datetime.min.replace(tzinfo=UTC)) >= cutoff]
        by_event: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for event in events:
            event_type = str(event.get("event_type") or "unknown")
            reason = str(event.get("reason") or "")
            by_event[event_type] = by_event.get(event_type, 0) + 1
            if reason:
                by_reason[reason] = by_reason.get(reason, 0) + 1
        active_blocks = sum(
            1 for record in snapshot.get("abuse_blocks", {}).values()
            if isinstance(record, dict) and (_as_datetime(record.get("blocked_until")) or current) > current
        )
        return {"active_blocks": active_blocks, "events_24h": dict(sorted(by_event.items())), "reasons_24h": dict(sorted(by_reason.items()))}


def blocked_message(language: str, blocked_until: datetime | None = None) -> str:
    lang = language if language in {"de", "ar", "en", "uk", "el"} else "de"
    return {
        "ar": "وصلتني رسائل كثيرة بسرعة، فوقفنا المعالجة لحظة لحماية حسابك والخدمة. استنى دقيقة وبعدين ابعت رسالة واحدة واضحة 🙏",
        "de": "Es kamen sehr viele Nachrichten in kurzer Zeit. Zum Schutz deines Kontos und des Dienstes pausiert die Verarbeitung kurz. Warte bitte eine Minute und sende dann eine klare Nachricht.",
        "en": "Many messages arrived very quickly, so processing is paused briefly to protect your account and the service. Please wait a minute, then send one clear message.",
        "uk": "Надійшло забагато повідомлень за короткий час. Обробку ненадовго призупинено для захисту акаунта і сервісу. Зачекай хвилину й надішли одне чітке повідомлення.",
        "el": "Έφτασαν πάρα πολλά μηνύματα σε λίγο χρόνο. Η επεξεργασία σταμάτησε προσωρινά για προστασία του λογαριασμού και της υπηρεσίας. Περίμενε ένα λεπτό και στείλε ένα σαφές μήνυμα.",
    }[lang]
