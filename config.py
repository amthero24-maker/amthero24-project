"""Environment-backed configuration for AmtHero24."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_TEXT_MODEL = "openai/gpt-oss-120b"
_DEPRECATED_TEXT_MODELS = {
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
}
_requested_text_model = os.getenv("GROQ_MODEL", _DEFAULT_TEXT_MODEL).strip()
GROQ_MODEL = (
    _DEFAULT_TEXT_MODEL
    if not _requested_text_model or _requested_text_model in _DEPRECATED_TEXT_MODELS
    else _requested_text_model
)

_DEFAULT_VISION_MODEL = "qwen/qwen3.6-27b"
_DEPRECATED_VISION_MODELS = {
    "llama-3.2-11b-vision-preview",
    "llama-3.2-90b-vision-preview",
    "meta-llama/llama-4-scout-17b-16e-instruct",
}
_requested_vision_model = os.getenv("GROQ_VISION_MODEL", _DEFAULT_VISION_MODEL).strip()
GROQ_VISION_MODEL = (
    _DEFAULT_VISION_MODEL
    if not _requested_vision_model or _requested_vision_model in _DEPRECATED_VISION_MODELS
    else _requested_vision_model
)

WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0").strip()
DATA_STORE_PATH = Path(os.getenv("DATA_STORE_PATH", "data/store.json"))
APP_VERSION = "3.7.0"
MAX_WHATSAPP_TEXT_LENGTH = 4096
MAX_MEDIA_BYTES = 20 * 1024 * 1024


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
