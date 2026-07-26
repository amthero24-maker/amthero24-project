"""Groq integration boundary."""
from __future__ import annotations

import base64
import logging

from groq import Groq

from config import GROQ_MODEL, GROQ_VISION_MODEL, required_env

logger = logging.getLogger(__name__)


class GroqServiceError(RuntimeError):
    pass


def generate_reply(*, system_prompt: str, user_text: str, image_bytes: bytes | None = None, mime_type: str = "image/jpeg") -> str:
    try:
        client = Groq(api_key=required_env("GROQ_API_KEY"))
        if image_bytes:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content: object = [
                {"type": "text", "text": user_text or "Please explain this document."},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
            ]
            model = GROQ_VISION_MODEL
        else:
            content = user_text or "Hello"
            model = GROQ_MODEL
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            max_tokens=1400,
            temperature=0.35,
        )
        reply = (completion.choices[0].message.content or "").strip()
        if not reply:
            raise GroqServiceError("Groq returned an empty response")
        return reply
    except GroqServiceError:
        raise
    except Exception as exc:
        logger.exception("Groq request failed")
        raise GroqServiceError("Groq request failed") from exc
