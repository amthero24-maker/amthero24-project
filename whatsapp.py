"""WhatsApp Cloud API client."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from config import MAX_MEDIA_BYTES, MAX_WHATSAPP_TEXT_LENGTH, WHATSAPP_API_VERSION, required_env

logger = logging.getLogger(__name__)


class WhatsAppServiceError(RuntimeError):
    pass


_http_client: httpx.AsyncClient | None = None


def split_message(text: str, limit: int = MAX_WHATSAPP_TEXT_LENGTH) -> Iterator[str]:
    """Split text without cutting normal words, URLs, or paragraphs when possible."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    remaining = str(text or "").strip()
    minimum_soft_break = max(1, limit // 2)

    while remaining:
        if len(remaining) <= limit:
            yield remaining
            return

        window = remaining[: limit + 1]
        split_at = -1
        for separator in ("\n\n", "\n", " "):
            candidate = window.rfind(separator, minimum_soft_break, limit + 1)
            if candidate > split_at:
                split_at = candidate
        if split_at <= 0:
            split_at = limit

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = limit
        yield chunk
        remaining = remaining[split_at:].lstrip()


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {required_env('WHATSAPP_TOKEN')}", "Content-Type": "application/json"}


def _messages_url() -> str:
    phone_id = required_env("PHONE_NUMBER_ID")
    return f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{phone_id}/messages"


def _get_http_client() -> httpx.AsyncClient:
    """Reuse one connection pool to avoid a new TLS handshake for every reply."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0),
        )
    return _http_client


async def close_http_client() -> None:
    """Close the shared client during application shutdown or isolated tests."""
    global _http_client
    client = _http_client
    _http_client = None
    if client is not None and not client.is_closed:
        await client.aclose()


async def _post_message(payload: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(2):
        try:
            response = await _get_http_client().post(_messages_url(), headers=_headers(), json=payload)
            response.raise_for_status()
            parsed = response.json()
            return parsed if isinstance(parsed, dict) else {}
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if attempt == 0:
                logger.warning("WhatsApp connection failed; retrying once")
                await asyncio.sleep(0.25)
                continue
            logger.exception("WhatsApp send failed")
            raise WhatsAppServiceError("WhatsApp send failed") from exc
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            logger.exception("WhatsApp send failed")
            raise WhatsAppServiceError("WhatsApp send failed") from exc
    raise WhatsAppServiceError("WhatsApp send failed")


async def send_whatsapp_message(to: str, text: str) -> list[dict]:
    if not to:
        raise WhatsAppServiceError("Missing recipient")
    if not str(text or "").strip():
        raise WhatsAppServiceError("Message text is empty")
    responses: list[dict] = []
    for chunk in split_message(text):
        responses.append(await _post_message({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": chunk, "preview_url": False},
        }))
    return responses


async def send_whatsapp_template(
    to: str,
    template_name: str,
    language_code: str,
    body_parameters: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Send an already-approved Meta utility template."""
    if not to:
        raise WhatsAppServiceError("Missing recipient")
    safe_name = str(template_name or "").strip()
    safe_language = str(language_code or "").strip()
    if not safe_name or not safe_language:
        raise WhatsAppServiceError("Missing template configuration")
    components: list[dict[str, Any]] = []
    parameters = [
        {"type": "text", "text": str(value or " ")[:1024]}
        for value in body_parameters
    ]
    if parameters:
        components.append({"type": "body", "parameters": parameters})
    return await _post_message({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": safe_name,
            "language": {"code": safe_language},
            "components": components,
        },
    })


async def get_media_url(media_id: str) -> str:
    if not media_id:
        raise WhatsAppServiceError("Missing media ID")
    try:
        response = await _get_http_client().get(
            f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}",
            headers=_headers(),
        )
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
        response = await _get_http_client().get(media_url, headers=_headers(), timeout=30.0)
        response.raise_for_status()
        content = response.content
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.exception("WhatsApp media download failed")
        raise WhatsAppServiceError("Media download failed") from exc
    if len(content) > MAX_MEDIA_BYTES:
        raise WhatsAppServiceError("Media exceeds safe size limit")
    return content
