"""Small, thread-safe and atomic JSON store for webhook processing state."""
import hashlib
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

def utc_now() -> datetime:
    return datetime.now(UTC)

class JsonDataStore:
    """Persist minimal user and message metadata in one JSON document."""
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def phone_hash(phone: str) -> str:
        return hashlib.sha256(phone.encode()).hexdigest()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"users": {}, "messages": {}, "cases": {}, "audit_log": []}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        with self.path.open(encoding="utf-8") as file:
            data = json.load(file)
        base = self._empty()
        base.update(data)
        return base

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self.path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._read_unlocked()

    def claim_message(self, message_id: str, phone: str) -> bool:
        """Atomically claim a webhook message; return false for a duplicate."""
        with self._lock:
            data = self._read_unlocked()
            if message_id in data["messages"]:
                return False
            now = utc_now()
            user_id = self.phone_hash(phone)
            data["users"].setdefault(user_id, {"created_at": now.isoformat(), "plan": "free"})
            data["messages"][message_id] = {
                "phone_hash": user_id, "status": "processing",
                "created_at": now.isoformat(),
                "delete_at": (now + timedelta(hours=24)).isoformat(),
            }
            data["audit_log"].append({"event_type": "message_claimed", "message_id": message_id, "timestamp": now.isoformat()})
            self._write_unlocked(data)
            return True

    def set_message_status(self, message_id: str, status: str) -> None:
        with self._lock:
            data = self._read_unlocked()
            if message_id not in data["messages"]:
                return
            now = utc_now().isoformat()
            data["messages"][message_id].update(status=status, updated_at=now)
            data["audit_log"].append({"event_type": status, "message_id": message_id, "timestamp": now})
            self._write_unlocked(data)

    def cleanup_expired(self, now: datetime | None = None) -> int:
        """Delete expired free-message metadata and return the count."""
        cutoff = now or utc_now()
        with self._lock:
            data = self._read_unlocked()
            expired = [key for key, item in data["messages"].items() if datetime.fromisoformat(item["delete_at"]) <= cutoff]
            for key in expired:
                del data["messages"][key]
            if expired:
                data["audit_log"].append({"event_type": "expired_messages_deleted", "count": len(expired), "timestamp": cutoff.isoformat()})
                self._write_unlocked(data)
            return len(expired)
