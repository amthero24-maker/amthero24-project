"""FastAPI webhook for the AmtHero24 WhatsApp assistant."""

import json
import logging
import os
from typing import Any
from urllib import request

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Response
from groq import Groq

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="AmtHero24 WhatsApp Webhook", version="1.1.0")

GROQ_MODEL = "llama-3.3-70b-versatile"
GRAPH_API_VERSION = "v22.0"
SYSTEM_PROMPT = (
    "Du bist AmtHero24, ein ruhiger und verständlicher Assistent für den Alltag "
    "in Deutschland. Antworte knapp, hilfreich und in der Sprache der Person. "
    "Kennzeichne Unsicherheit und behaupte nie, eine Rechtsberatung zu ersetzen."
)


def required_env(name: str, fallback: str | None = None) -> str:
    """Return a non-empty environment value or fail with a clear message."""
    value = os.getenv(name)
    if value:
        return value

    if fallback:
        fallback_value = os.getenv(fallback)
        if fallback_value:
            return fallback_value

    fallback_hint = f" or {fallback}" if fallback else ""
    raise RuntimeError(f"Missing required environment variable: {name}{fallback_hint}")


def extract_text_messages(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract sender/text pairs from a WhatsApp Cloud API webhook payload."""
    messages: list[tuple[str, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                sender = message.get("from")
                text = message.get("text", {}).get("body")
                if message.get("type") == "text" and sender and text:
                    messages.append((sender, text.strip()))
    return messages


def generate_reply(message: str) -> str:
    """Generate an AmtHero24 response with Groq."""
    client = Groq(api_key=required_env("GROQ_API_KEY", "OPENAI_API_KEY"))
    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
        temperature=0.2,
        max_tokens=700,
    )
    reply = completion.choices[0].message.content
    if not reply:
        raise RuntimeError("Groq returned an empty response")
    return reply


def send_whatsapp_message(recipient: str, text: str) -> None:
    """Send one text response through the WhatsApp Cloud API."""
    phone_number_id = required_env("PHONE_NUMBER_ID")
    token = required_env("WHATSAPP_TOKEN")
    url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
        f"{phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    body = json.dumps(payload).encode("utf-8")
    webhook_request = request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(webhook_request, timeout=15) as response:
        if response.status >= 300:
            raise RuntimeError(f"WhatsApp API returned HTTP {response.status}")


def process_message(sender: str, message: str) -> None:
    """Generate and deliver a reply outside the webhook request lifecycle."""
    try:
        send_whatsapp_message(sender, generate_reply(message))
    except Exception:
        logger.exception("Unable to process WhatsApp message from %s", sender)


@app.get("/webhook", response_class=Response)
def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    """Complete Meta's webhook verification challenge."""
    if mode == "subscribe" and verify_token == required_env("VERIFY_TOKEN"):
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook")
def receive_webhook(
    payload: dict[str, Any], background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Acknowledge immediately and process incoming text messages afterward."""
    for sender, message in extract_text_messages(payload):
        background_tasks.add_task(process_message, sender, message)
    return {"status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    """Expose a dependency-free liveness endpoint."""
    return {"status": "ok"}
