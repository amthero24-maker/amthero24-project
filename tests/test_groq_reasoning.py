"""Groq response safety tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import groq_client


def test_sanitize_removes_closed_think_block() -> None:
    raw = "<think>private analysis</think>\nالرسالة تطلب منك دفع القسط بعد وصول الراتب."
    assert groq_client.sanitize_model_reply(raw) == "الرسالة تطلب منك دفع القسط بعد وصول الراتب."


def test_sanitize_rejects_unterminated_reasoning() -> None:
    with pytest.raises(groq_client.GroqServiceError):
        groq_client.sanitize_model_reply("<think>internal analysis")


def test_qwen_request_hides_reasoning(monkeypatch) -> None:
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="شرح مختصر وواضح."))])
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    with patch.object(groq_client, "Groq", return_value=client), patch.object(groq_client, "GROQ_VISION_MODEL", "qwen/qwen3.6-27b"):
        result = groq_client.generate_reply(
            system_prompt="Reply in Arabic.",
            user_text="اشرح الصورة",
            image_bytes=b"image",
            mime_type="image/jpeg",
        )

    assert result == "شرح مختصر وواضح."
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["reasoning_format"] == "hidden"
    assert kwargs["reasoning_effort"] == "none"
