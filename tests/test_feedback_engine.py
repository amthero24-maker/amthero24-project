"""Anonymous Beta feedback tests."""
from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore
from feedback_engine import FeedbackRepository, detect_feedback, feedback_ack


def test_feedback_detection_is_explicit_and_compact() -> None:
    assert detect_feedback("👍") == 5
    assert detect_feedback("مو مفيد") == 1
    assert detect_feedback("Rating 4 out of 5") == 4
    assert detect_feedback("عندي 5 فواتير وبدي مساعدة") is None
    assert detect_feedback("هذا شرح طويل وليس تقييمًا " * 10) is None


def test_feedback_repository_stores_no_identity_or_content(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    repository = FeedbackRepository(store)
    event = repository.record(5, language="ar", topic="invoice")

    assert event["score"] == 5
    assert set(event) == {"feedback_id", "score", "language", "topic", "source", "created_at"}
    serialized = str(store.snapshot())
    assert "49123" not in serialized
    assert "phone_hash" not in serialized
    assert "message" not in serialized


def test_feedback_aggregate_and_cleanup(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    repository = FeedbackRepository(store)
    repository.record(5, language="ar", topic="document")
    repository.record(4, language="de", topic="document")
    repository.record(1, language="en", topic="work")

    aggregate = repository.aggregate(days=30)
    assert aggregate["responses"] == 3
    assert aggregate["average_score"] == 3.33
    assert aggregate["positive_rate_percent"] == 66.7
    assert aggregate["topics"] == {"document": 2, "work": 1}

    snapshot = store.snapshot()
    snapshot["anonymous_feedback"][0]["created_at"] = (datetime.now(UTC) - timedelta(days=500)).isoformat()
    with store._lock:
        store._write_unlocked(snapshot)
    assert repository.cleanup(days=365) == 1
    assert repository.aggregate(days=365)["responses"] == 2


def test_feedback_ack_is_localized() -> None:
    assert "مجهول" in feedback_ack("ar", 5)
    assert "anonym" in feedback_ack("de", 1).casefold()
