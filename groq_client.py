"""Groq integration boundary."""
from __future__ import annotations

import base64
import logging
import re

from groq import Groq

from config import GROQ_MODEL, GROQ_VISION_MODEL, required_env

logger = logging.getLogger(__name__)


class GroqServiceError(RuntimeError):
    pass


def sanitize_model_reply(value: str) -> str:
    """Remove model reasoning traces before anything reaches WhatsApp."""
    reply = (value or "").strip()

    # Qwen raw reasoning can be embedded inside <think>...</think>.
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.IGNORECASE | re.DOTALL).strip()

    # Never expose an unterminated reasoning block or obvious internal monologue.
    lowered = reply.casefold()
    internal_markers = (
        "<think>",
        "here's a thinking process",
        "here is a thinking process",
        "analyze user input",
        "identify key constraints",
        "let's verify the content matches",
        "the prompt says",
        "i will output it now",
    )
    if any(marker in lowered for marker in internal_markers):
        logger.error("Model returned internal reasoning instead of a final answer")
        raise GroqServiceError("Model returned an unsafe reasoning trace")

    if not reply:
        raise GroqServiceError("Groq returned an empty response")
    return reply


def generate_reply(*, system_prompt: str, user_text: str, image_bytes: bytes | None = None, mime_type: str = "image/jpeg") -> str:
    try:
        client = Groq(api_key=required_env("GROQ_API_KEY"))
        if image_bytes:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content: object = [
                {"type": "text", "text": user_text or "Explain this document concisely in the user's preferred language. Return only the final answer."},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
            ]
            model = GROQ_VISION_MODEL
        else:
            content = user_text or "Hello"
            model = GROQ_MODEL

        request: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "max_tokens": 1400,
            "temperature": 0.35,
        }
        if model.startswith("qwen/"):
            # Groq documents `hidden` as final-answer-only and `none` as
            # non-thinking mode for Qwen 3 models.
            request["reasoning_format"] = "hidden"
            request["reasoning_effort"] = "none"

        completion = client.chat.completions.create(**request)
        return sanitize_model_reply(completion.choices[0].message.content or "")
    except GroqServiceError:
        raise
    except Exception as exc:
        logger.exception("Groq request failed")
        raise GroqServiceError("Groq request failed") from exc
