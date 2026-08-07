"""Regression coverage for bounded WhatsApp connection retries."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import whatsapp


@pytest.mark.anyio
async def test_connect_timeout_retries_once_then_returns_original_response() -> None:
    response = MagicMock()
    response.json.return_value = {"messages": [{"id": "retry-ok"}]}
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = [httpx.ConnectTimeout("connect timed out"), response]

    with patch.object(whatsapp, "_http_client", client), patch.object(
        whatsapp.asyncio, "sleep", new=AsyncMock()
    ) as sleep, patch.dict(
        os.environ,
        {"WHATSAPP_TOKEN": "secret", "PHONE_NUMBER_ID": "phone-123"},
        clear=True,
    ):
        result = await whatsapp.send_whatsapp_message("49123", "Hallo")

    assert result == [{"messages": [{"id": "retry-ok"}]}]
    assert client.post.await_count == 2
    sleep.assert_awaited_once_with(0.25)
    response.raise_for_status.assert_called_once()


@pytest.mark.anyio
async def test_connect_error_retries_only_once_then_fails_closed() -> None:
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = httpx.ConnectError("down")

    with patch.object(whatsapp, "_http_client", client), patch.object(
        whatsapp.asyncio, "sleep", new=AsyncMock()
    ) as sleep, patch.dict(
        os.environ,
        {"WHATSAPP_TOKEN": "secret", "PHONE_NUMBER_ID": "phone-123"},
        clear=True,
    ), pytest.raises(whatsapp.WhatsAppServiceError):
        await whatsapp.send_whatsapp_message("49123", "Hallo")

    assert client.post.await_count == 2
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.anyio
async def test_read_timeout_is_not_retried_to_avoid_ambiguous_duplicate_send() -> None:
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = httpx.ReadTimeout("response timed out")

    with patch.object(whatsapp, "_http_client", client), patch.object(
        whatsapp.asyncio, "sleep", new=AsyncMock()
    ) as sleep, patch.dict(
        os.environ,
        {"WHATSAPP_TOKEN": "secret", "PHONE_NUMBER_ID": "phone-123"},
        clear=True,
    ), pytest.raises(whatsapp.WhatsAppServiceError):
        await whatsapp.send_whatsapp_message("49123", "Hallo")

    assert client.post.await_count == 1
    sleep.assert_not_awaited()
