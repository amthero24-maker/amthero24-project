"""Groq response and model safety tests."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import config
import groq_client


def test_sanitize_removes_closed_think_block() -> None:
    raw = "<think>private analysis</think>\nالرسالة تطلب منك دفع القسط بعد وصول الراتب."
    assert groq_client.sanitize_model_reply(raw) == "الرسالة تطلب منك دفع القسط بعد وصول الراتب."


def test_sanitize_rejects_unterminated_reasoning() -> None:
    with pytest.raises(groq_client.GroqServiceError):
        groq_client.sanitize_model_reply("<think>internal analysis")


def test_sanitize_removes_isolated_ocr_ideograph() -> None:
    assert groq_client.sanitize_model_reply("أبلغت الشركة أن دفع率 الفاتورة سيتأخر.") == "أبلغت الشركة أن دفع الفاتورة سيتأخر."


def test_deprecated_text_model_environment_is_migrated(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    reloaded = importlib.reload(config)
    assert reloaded.GROQ_MODEL == "openai/gpt-oss-120b"


def test_qwen_request_hides_reasoning_and_caps_vision_output(monkeypatch) -> None:
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
    assert kwargs["max_tokens"] == 650


def test_gpt_oss_text_request_excludes_reasoning(monkeypatch) -> None:
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="جواب نهائي فقط."))])
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    with patch.object(groq_client, "Groq", return_value=client), patch.object(groq_client, "GROQ_MODEL", "openai/gpt-oss-120b"):
        result = groq_client.generate_reply(
            system_prompt="Reply in Arabic.",
            user_text="ساعدني",
        )

    assert result == "جواب نهائي فقط."
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-oss-120b"
    assert kwargs["include_reasoning"] is False
    assert kwargs["reasoning_effort"] == "low"
    assert "reasoning_format" not in kwargs
