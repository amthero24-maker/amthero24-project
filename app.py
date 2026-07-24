import json
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(title="AmtHero24 WhatsApp Bot")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
META_GRAPH_API_VERSION = "v20.0"
SYSTEM_PROMPT = (
    "You are AmtHero24 V2, big brother from Aachen, output only JSON with "
    "formal_de and explanation_native starting with --- شرح بلغتك ---"
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(status_code=500, detail=f"Missing environment variable: {name}")
    return value


def extract_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                sender = message.get("from")
                text = message.get("text", {}).get("body")
                if sender and text:
                    messages.append({"from": sender, "text": text})

    return messages


def render_reply(groq_content: str) -> str:
    try:
        data = json.loads(groq_content)
    except json.JSONDecodeError:
        return groq_content

    formal_de = str(data.get("formal_de", "")).strip()
    explanation_native = str(data.get("explanation_native", "")).strip()

    if formal_de and explanation_native:
        return f"{formal_de}\n\n{explanation_native}"
    return groq_content


async def ask_groq(user_message: str) -> str:
    api_key = required_env("GROQ_API_KEY")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(GROQ_API_URL, headers=headers, json=body)
        response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    return render_reply(content)


async def send_whatsapp_message(to: str, text: str) -> None:
    token = required_env("WHATSAPP_TOKEN")
    phone_number_id = required_env("PHONE_NUMBER_ID")
    url = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()


@app.get("/webhook")
async def verify_webhook(request: Request) -> Response:
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == required_env("VERIFY_TOKEN") and challenge is not None:
        return Response(content=challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_webhook(request: Request) -> dict[str, str]:
    payload = await request.json()
    messages = extract_messages(payload)

    for message in messages:
        try:
            reply = await ask_groq(message["text"])
            await send_whatsapp_message(message["from"], reply)
        except httpx.HTTPStatusError as exc:
            logger.exception("External API returned an error: %s", exc.response.text)
        except Exception:
            logger.exception("Failed to process WhatsApp message")

    return {"status": "ok"}
