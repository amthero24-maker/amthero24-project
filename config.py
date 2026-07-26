"""Environment-backed configuration for AmtHero24."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "llama-3.2-11b-vision-preview").strip()
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0").strip()
DATA_STORE_PATH = Path(os.getenv("DATA_STORE_PATH", "data/store.json"))
APP_VERSION = "1.5.0"
MAX_WHATSAPP_TEXT_LENGTH = 4096
MAX_MEDIA_BYTES = 20 * 1024 * 1024


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
