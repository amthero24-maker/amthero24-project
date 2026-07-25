"""Environment-backed configuration for AmtHero24."""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
GROQ_MODEL = "llama-3.3-70b-versatile"
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "1264010770128749")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
DATA_STORE_PATH = Path(os.getenv("DATA_STORE_PATH", "data/store.json"))
def required_env(name: str) -> str:
    """Return a required secret without resolving it during module import."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
