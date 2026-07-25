"""WhatsApp Cloud client tests."""
import os
from unittest.mock import MagicMock, patch
import whatsapp

def test_split_long_message() -> None:
    assert list(map(len, whatsapp.split_message("x" * 5000))) == [4096, 904]

def test_send_uses_registered_phone_id() -> None:
    response = MagicMock()
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = response
    with patch.object(whatsapp.httpx, "Client", return_value=client), patch.dict(os.environ, {"WHATSAPP_TOKEN": "secret"}, clear=True):
        whatsapp.send_whatsapp_message("49123", "Hallo")
    url = client.post.call_args.args[0]
    assert "/1264010770128749/messages" in url
    assert client.post.call_args.kwargs["json"]["to"] == "49123"
    response.raise_for_status.assert_called_once()
