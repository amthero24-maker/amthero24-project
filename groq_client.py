"""Groq integration boundary."""
from __future__ import annotations

import base64
import logging
import re
import unicodedata

from groq import Groq

from brief_scanner_app_hook import decide_brief_scanner_media_flow
from config import GROQ_MODEL, GROQ_VISION_MODEL, required_env

logger = logging.getLogger(__name__)


class GroqServiceError(RuntimeError):
    pass


_REPLY_LANGUAGE_CODES = {
    "german": "de",
    "arabic": "ar",
    "english": "en",
    "ukrainian": "uk",
    "greek": "el",
}


def _response_language_from_prompt(system_prompt: str) -> str | None:
    """Read the deterministic reply-language marker emitted by build_system_prompt."""
    if type(system_prompt) is not str:
        return None
    match = re.search(
        r"current reply language is\s+([A-Za-z]+)\s*[.;]",
        system_prompt,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _REPLY_LANGUAGE_CODES.get(match.group(1).casefold())


def sanitize_model_reply(value: str) -> str:
    """Remove reasoning traces and malformed Unicode before WhatsApp delivery."""
    reply = (value or "").strip()

    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.IGNORECASE | re.DOTALL).strip()

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

    reply = unicodedata.normalize("NFKC", reply)
    reply = "".join(ch for ch in reply if unicodedata.category(ch) not in {"Cf", "Cs"} or ch in {"\n", "\t"})

    reply = re.sub(r"(?<![\u3400-\u9fff])[\u3400-\u9fff](?![\u3400-\u9fff])", "", reply)
    reply = re.sub(r"[ \t]{2,}", " ", reply)
    reply = re.sub(r" *\n *", "\n", reply).strip()

    if not reply:
        raise GroqServiceError("Groq returned an empty response")
    return reply


def generate_reply(*, system_prompt: str, user_text: str, image_bytes: bytes | None = None, mime_type: str = "image/jpeg") -> str:
    try:
        if image_bytes:
            response_language = _response_language_from_prompt(system_prompt)
            if response_language is not None:
                scanner_decision = decide_brief_scanner_media_flow(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    response_language=response_language,
                )
                if not scanner_decision.use_existing_flow:
                    return sanitize_model_reply(scanner_decision.reply)

        client = Groq(api_key=required_env("GROQ_API_KEY"))
        if image_bytes:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content: object = [
                {
                    "type": "text",
                    "text": user_text or (
                        "Explain this document in the user's preferred language. "
                        "Use no more than 700 characters and at most three short sections: "
                        "meaning, important point, next step. Return only the final answer."
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
            ]
            model = GROQ_VISION_MODEL
            max_tokens = 650
        else:
            content = user_text or "Hello"
            model = GROQ_MODEL
            max_tokens = 900

        request: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        if model.startswith("qwen/"):
            request["reasoning_format"] = "hidden"
            request["reasoning_effort"] = "none"
        elif model.startswith("openai/gpt-oss-"):
            request["include_reasoning"] = False
            request["reasoning_effort"] = "low"

        completion = client.chat.completions.create(**request)
        return sanitize_model_reply(completion.choices[0].message.content or "")
    except GroqServiceError:
        raise
    except Exception as exc:
        logger.error("Groq request failed")
        raise GroqServiceError("Groq request failed") from exc
