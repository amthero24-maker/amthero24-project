"""Outbound WhatsApp Cloud API client."""
import httpx
from config import PHONE_NUMBER_ID, WHATSAPP_API_VERSION, required_env
MAX_TEXT_LENGTH = 4096

def split_message(text: str, limit: int = MAX_TEXT_LENGTH) -> list[str]:
    """Split long replies into valid WhatsApp text-message chunks."""
    return [text[index:index + limit] for index in range(0, len(text), limit)] or [""]

def send_whatsapp_message(recipient: str, text: str) -> None:
    """Send text through Meta Graph API, raising on unsuccessful responses."""
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {required_env('WHATSAPP_TOKEN')}"}
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        for chunk in split_message(text):
            response = client.post(url, headers=headers, json={
                "messaging_product": "whatsapp", "recipient_type": "individual",
                "to": recipient, "type": "text",
                "text": {"preview_url": False, "body": chunk},
            })
            response.raise_for_status()
