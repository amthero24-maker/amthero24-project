"""WhatsApp Cloud API client."""
from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx

from config import MAX_MEDIA_BYTES, MAX_WHATSAPP_TEXT_LENGTH, WHATSAPP_API_VERSION, required_env

logger = logging.getLogger(__name__)


class WhatsAppServiceError(RuntimeError):
    pass


def split_message(text: str, limit: int = MAX_WHATSAPP_TEXT_LENGTH) -> Iterator[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    for start in range(0, len(text), limit):
        yield text[start:start + limit]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {required_env('WHATSAPP_TOKEN')}", "Content-Type": "application/json"}


async def send_whatsapp_message(to: str, text: str) -> list[dict]:
    if not to:
        raise WhatsAppServiceError("Missing recipient")
    phone_id = required_env("PHONE_NUMBER_ID")
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_id}/messages"
    responses: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for chunk in split_message(text):
                response = await client.post(url, headers=_headers(), json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": chunk},
                })
                response.raise_for_status()
                responses.append(response.json())
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        logger.exception("WhatsApp send failed")
        raise WhatsAppServiceError("WhatsApp send failed") from exc
    return responses


async def get_media_url(media_id: str) -> str:
    if not media_id:
        raise WhatsAppServiceError("Missing media ID")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}", headers=_headers())
            response.raise_for_status()
            url = str(response.json().get("url", ""))
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        logger.exception("WhatsApp media metadata request failed")
        raise WhatsAppServiceError("Media metadata request failed") from exc
    if not url:
        raise WhatsAppServiceError("Media URL missing")
    return url


async def download_media_bytes(media_url: str) -> bytes:
    if not media_url:
        raise WhatsAppServiceError("Missing media URL")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(media_url, headers=_headers())
            response.raise_for_status()
            content = response.content
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.exception("WhatsApp media download failed")
        raise WhatsAppServiceError("Media download failed") from exc
    if len(content) > MAX_MEDIA_BYTES:
        raise WhatsAppServiceError("Media exceeds safe size limit")
    return content
