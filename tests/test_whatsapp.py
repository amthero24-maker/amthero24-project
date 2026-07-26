"""WhatsApp Cloud client tests."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import whatsapp


def test_split_long_unbroken_message_uses_hard_limit() -> None:
    assert list(map(len, whatsapp.split_message("x" * 5000))) == [4096, 904]


def test_split_prefers_paragraph_and_word_boundaries() -> None:
    text = "مقدمة قصيرة\n\n" + ("كلمة " * 40) + "\n\nالخاتمة"
    chunks = list(whatsapp.split_message(text, limit=90))
    assert len(chunks) > 1
    assert all(len(chunk) <= 90 for chunk in chunks)
    assert all(not chunk.startswith(" ") and not chunk.endswith(" ") for chunk in chunks)
    assert "مقدمة قصيرة" in chunks[0]
    assert chunks[-1].endswith("الخاتمة")


def test_split_rejects_invalid_limit_and_empty_text_yields_nothing() -> None:
    with pytest.raises(ValueError):
        list(whatsapp.split_message("text", limit=0))
    assert list(whatsapp.split_message("   ")) == []


@pytest.mark.anyio
async def test_send_uses_environment_phone_id() -> None:
    response = MagicMock()
    response.json.return_value = {"messages": [{"id": "1"}]}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = response
    with patch.object(whatsapp.httpx, "AsyncClient", return_value=client), patch.dict(
        os.environ,
        {"WHATSAPP_TOKEN": "secret", "PHONE_NUMBER_ID": "phone-123"},
        clear=True,
    ):
        result = await whatsapp.send_whatsapp_message("49123", "Hallo")
    assert result == [{"messages": [{"id": "1"}]}]
    url = client.post.call_args.args[0]
    assert "/phone-123/messages" in url
    payload = client.post.call_args.kwargs["json"]
    assert payload["to"] == "49123"
    assert payload["text"]["preview_url"] is False
    response.raise_for_status.assert_called_once()


@pytest.mark.anyio
async def test_send_template_uses_approved_name_language_and_parameters() -> None:
    response = MagicMock()
    response.json.return_value = {"messages": [{"id": "template-1"}]}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = response
    with patch.object(whatsapp.httpx, "AsyncClient", return_value=client), patch.dict(
        os.environ,
        {"WHATSAPP_TOKEN": "secret", "PHONE_NUMBER_ID": "phone-123"},
        clear=True,
    ):
        result = await whatsapp.send_whatsapp_template(
            "49123", "amthero24_mission_reminder", "de", ["Wissam", "WKK", "10.08.2026"]
        )
    assert result == {"messages": [{"id": "template-1"}]}
    payload = client.post.call_args.kwargs["json"]
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "amthero24_mission_reminder"
    assert payload["template"]["language"]["code"] == "de"
    assert [item["text"] for item in payload["template"]["components"][0]["parameters"]] == [
        "Wissam", "WKK", "10.08.2026"
    ]


@pytest.mark.anyio
async def test_send_empty_text_is_rejected_before_network() -> None:
    with pytest.raises(whatsapp.WhatsAppServiceError):
        await whatsapp.send_whatsapp_message("49123", "   ")


@pytest.mark.anyio
async def test_send_failure_is_wrapped() -> None:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = httpx.ConnectError("down")
    with patch.object(whatsapp.httpx, "AsyncClient", return_value=client), patch.dict(
        os.environ,
        {"WHATSAPP_TOKEN": "secret", "PHONE_NUMBER_ID": "phone-123"},
        clear=True,
    ), pytest.raises(whatsapp.WhatsAppServiceError):
        await whatsapp.send_whatsapp_message("49123", "Hallo")
