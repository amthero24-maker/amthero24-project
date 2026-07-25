"""Tests for webhook parsing and asynchronous dispatch."""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("VERIFY_TOKEN", "qa-token")

from fastapi import BackgroundTasks

from app import extract_text_messages, receive_webhook, required_env, verify_webhook


def test_extract_text_messages_ignores_statuses_and_non_text_messages() -> None:
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "statuses": [{"status": "delivered"}],
                    "messages": [
                        {"from": "49123", "type": "text", "text": {"body": "Hallo"}},
                        {"from": "49123", "type": "image", "image": {"id": "1"}},
                    ],
                }
            }]
        }]
    }

    assert extract_text_messages(payload) == [("49123", "Hallo")]


def test_required_env_uses_named_fallback() -> None:
    with patch.dict(os.environ, {"OPENAI_API_KEY": "fallback-key"}, clear=True):
        assert required_env("GROQ_API_KEY", "OPENAI_API_KEY") == "fallback-key"


def test_required_env_rejects_missing_value() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="GROQ_API_KEY or OPENAI_API_KEY"):
            required_env("GROQ_API_KEY", "OPENAI_API_KEY")


def test_webhook_verification_returns_challenge() -> None:
    with patch.dict(os.environ, {"VERIFY_TOKEN": "qa-token"}, clear=True):
        response = verify_webhook("subscribe", "qa-token", "challenge-123")

    assert response.status_code == 200
    assert response.body == b"challenge-123"


def test_receive_webhook_queues_work_and_acknowledges_immediately() -> None:
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [
                        {"from": "49123", "type": "text", "text": {"body": "Hilfe"}}
                    ]
                }
            }]
        }]
    }
    tasks = BackgroundTasks()

    assert receive_webhook(payload, tasks) == {"status": "ok"}
    assert len(tasks.tasks) == 1
