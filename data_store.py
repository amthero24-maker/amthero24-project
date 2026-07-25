"""Thread-safe atomic JSON persistence until PostgreSQL migration."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _phone_hash(phone: str) -> str:
    normalized = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class JsonDataStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"users": {}, "messages": {}, "cases": {}, "audit_log": []}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        return parsed if isinstance(parsed, dict) else self._empty()

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _transaction(self, operation: Callable[[dict[str, Any]], T]) -> T:
        with self._lock:
            data = self._read_unlocked()
            for key, default in self._empty().items():
                data.setdefault(key, default)
            result = operation(data)
            self._write_unlocked(data)
            return result

    def claim_message(self, message_id: str, phone: str, text: str = "", *, message_type: str = "text", media_id: str | None = None) -> bool:
        if not message_id or not phone:
            return False

        def claim(data: dict[str, Any]) -> bool:
            if message_id in data["messages"]:
                return False
            now = _now()
            data["messages"][message_id] = {
                "phone_hash": _phone_hash(phone),
                "text": text[:2000],
                "type": message_type,
                "has_media": bool(media_id),
                "status": "claimed",
                "created_at": now,
                "updated_at": now,
            }
            return True

        return self._transaction(claim)

    def update_message_status(self, message_id: str, status: str) -> None:
        def update(data: dict[str, Any]) -> None:
            record = data["messages"].get(message_id)
            if record:
                record["status"] = status
                record["updated_at"] = _now()
        self._transaction(update)

    def get_user(self, phone: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._read_unlocked().get("users", {}).get(_phone_hash(phone), {}))

    def update_user(self, phone: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {"first_name", "city", "preferred_language", "last_seen", "last_message", "name_prompted"}
        clean = {key: value for key, value in updates.items() if key in allowed}
        key = _phone_hash(phone)

        def update(data: dict[str, Any]) -> dict[str, Any]:
            profile = data["users"].setdefault(key, {})
            profile.update(clean)
            profile["updated_at"] = _now()
            return deepcopy(profile)

        return self._transaction(update)

    def delete_user(self, phone: str) -> bool:
        key = _phone_hash(phone)

        def delete(data: dict[str, Any]) -> bool:
            existed = data["users"].pop(key, None) is not None
            for message_id in [mid for mid, record in data["messages"].items() if record.get("phone_hash") == key]:
                del data["messages"][message_id]
            return existed

        return self._transaction(delete)

    def recent_user_messages(self, phone: str, limit: int = 4) -> list[str]:
        key = _phone_hash(phone)
        with self._lock:
            records = [
                record
                for record in self._read_unlocked().get("messages", {}).values()
                if record.get("phone_hash") == key and record.get("text")
            ]
        records.sort(key=lambda record: record.get("created_at", ""))
        return [str(record["text"]) for record in records[-limit:]]

    def cleanup_expired(self, now: datetime | None = None, *, max_age: timedelta = timedelta(hours=24)) -> int:
        cutoff = (now or datetime.now(UTC)) - max_age

        def cleanup(data: dict[str, Any]) -> int:
            expired = []
            for message_id, record in data["messages"].items():
                try:
                    created = datetime.fromisoformat(record["created_at"])
                except (KeyError, TypeError, ValueError):
                    expired.append(message_id)
                    continue
                if created < cutoff:
                    expired.append(message_id)
            for message_id in expired:
                del data["messages"][message_id]
            return len(expired)

        return self._transaction(cleanup)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._read_unlocked())


_default_store = JsonDataStore(os.getenv("DATA_STORE_PATH", "data/store.json"))


def add_message(msg_id: str, sender: str, text: str) -> bool:
    return _default_store.claim_message(msg_id, sender, text)


def add_user(phone: str, data: dict[str, Any]) -> dict[str, Any]:
    return _default_store.update_user(phone, data)


def get_store() -> dict[str, Any]:
    return _default_store.snapshot()


def _load() -> dict[str, Any]:
    return _default_store.snapshot()


def _save_atomic(data: dict[str, Any]) -> None:
    with _default_store._lock:
        _default_store._write_unlocked(data)
