"""FastAPI webhook for the AmtHero24 WhatsApp assistant."""
import logging
import os
from typing import Any
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Response
from groq import Groq
from config import DATA_STORE_PATH, GROQ_MODEL, required_env
from data_store import JsonDataStore
from whatsapp import send_whatsapp_message
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
app = FastAPI(title="AmtHero24 WhatsApp Webhook", version="1.1.0")
store = JsonDataStore(DATA_STORE_PATH)
SYSTEM_PROMPT = """Du bist AmtHero24 — der ruhige Alltagsheld für Deutschland.
Antworte warm, knapp, handlungsorientiert und für Erklärungen in der Sprache der Person.
Offizielle kopierfertige Texte schreibst du ausschließlich in formellem Hochdeutsch und Sie-Form.
Danach folgt exakt der Trenner: --- شرح بلغتك:
Gib allgemeine Informationen und Formulierungshilfe, niemals eine Garantie oder Rechtsberatung.
Wenn du eine Rechtsquelle nicht sicher kennst, erfinde keinen Paragraphen.
Bei Gericht, Polizei, Haft, Gewalt, Räumung, Aufenthaltstitel, Jugendamt, Vollstreckung oder
existenzbedrohenden Gesundheits-/Leistungsthemen empfiehl zusätzlich dringend eine geeignete
Beratungsstelle oder anwaltliche Hilfe. Relevante Antworten schließen mit:
Keine Rechtsberatung. Nur allgemeine Infos."""

def extract_text_messages(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Extract message ID, sender and content from supported messages."""
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                message_id, sender = message.get("id"), message.get("from")
                content = message.get("text", {}).get("body")
                if message.get("type") == "button":
                    content = message.get("button", {}).get("text")
                elif message.get("type") == "interactive":
                    interactive = message.get("interactive", {})
                    content = (interactive.get("button_reply") or interactive.get("list_reply") or {}).get("title")
                if message_id and sender and isinstance(content, str) and content.strip():
                    messages.append((message_id, sender, content.strip()))
    return messages

def generate_reply(message: str) -> str:
    client = Groq(api_key=required_env("GROQ_API_KEY"))
    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": message}],
        temperature=0.2, max_tokens=900,
    )
    reply = completion.choices[0].message.content
    if not reply:
        raise RuntimeError("Groq returned an empty response")
    return reply

def process_message(message_id: str, sender: str, message: str) -> None:
    """Deduplicate, generate, and deliver a reply after acknowledgment."""
    if not store.claim_message(message_id, sender):
        logger.info("Ignoring duplicate WhatsApp message %s", message_id)
        return
    try:
        send_whatsapp_message(sender, generate_reply(message))
        store.set_message_status(message_id, "sent")
        store.cleanup_expired()
    except Exception:
        store.set_message_status(message_id, "failed")
        logger.exception("Unable to process WhatsApp message %s", message_id)

@app.get("/webhook", response_class=Response)
def verify_webhook(mode: str | None = Query(None, alias="hub.mode"), verify_token: str | None = Query(None, alias="hub.verify_token"), challenge: str | None = Query(None, alias="hub.challenge")) -> Response:
    if mode == "subscribe" and verify_token == required_env("VERIFY_TOKEN"):
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Webhook verification failed")

@app.post("/webhook")
def receive_webhook(payload: dict[str, Any], background_tasks: BackgroundTasks) -> dict[str, str]:
    """Return 200 without performing Groq or Graph API network calls inline."""
    for message_id, sender, message in extract_text_messages(payload):
        background_tasks.add_task(process_message, message_id, sender, message)
    return {"status": "accepted"}

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
