"""WhatsApp Cloud client tests."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import whatsapp


def test_split_long_message() -> None:
    assert list(map(len, whatsapp.split_message("x" * 5000))) == [4096, 904]


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
    assert client.post.call_args.kwargs["json"]["to"] == "49123"
    response.raise_for_status.assert_called_once()


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
