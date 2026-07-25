"""
AMTHERO24 OS v1.2.1 - Vision Stable - Crash Fix
Fix: Removed openai dependency at import time, safer startup
"""
from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import os
import re
import time
import base64
import httpx

# Config safe load
try:
    from config import VERIFY_TOKEN, GROQ_MODEL, GROQ_API_KEY, WHATSAPP_TOKEN, PHONE_NUMBER_ID
except Exception as e:
    print(f"Config import warning: {e}")
    VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "amthero24_verify_2025")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
    PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "1264010770128749")

try:
    from data_store import add_message, get_store, add_user
except Exception as e:
    print(f"data_store import warning: {e}")
    # fallback in-memory
    def add_message(*args, **kwargs): return True
    def get_store(): return {"users":{}}
    def add_user(*args, **kwargs): pass

try:
    from whatsapp import send_whatsapp_message
except Exception as e:
    print(f"whatsapp import warning: {e}")
    async def send_whatsapp_message(to, text):
        print(f"MOCK SEND to {to}: {text[:100]}")
        return {"mock": True}

app = FastAPI(title="AmtHero24 OS", version="1.2.1")

GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "llama-3.2-11b-vision-preview")
GRAPH_BASE = "https://graph.facebook.com/v21.0"

def detect_language(text: str) -> str:
    if not text:
        return "de"
    t = text.lower()
    if re.search(r'[\u0370-\u03FF]', text):
        return "el"
    if re.search(r'[\u0600-\u06FF]', text):
        return "ar"
    if any(w in t for w in ["pershendetje", "çfarë", "faleminderit", "përshëndetje"]):
        return "sq"
    if any(w in t for w in ["hello", "please", "thank you"]):
        return "en"
    return "de"

def get_user_profile(phone: str) -> dict:
    try:
        store = get_store()
        return store.get("users", {}).get(phone, {})
    except:
        return {}

def save_user_profile(phone: str, updates: dict):
    try:
        add_user(phone, updates)
    except Exception as e:
        print(f"save profile error: {e}")

async def get_media_url(media_id: str) -> str:
    if not WHATSAPP_TOKEN:
        print("WHATSAPP_TOKEN missing")
        return ""
    url = f"{GRAPH_BASE}/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data.get("url", "")

async def download_media_bytes(media_url: str) -> bytes:
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(media_url, headers=headers)
        r.raise_for_status()
        return r.content

MASTER_OS_PROMPT = """
# AMTHERO24 OS v1.2 - VISION EDITION
You are AmtHero24 - trusted WhatsApp AI for bureaucracy in Germany.
70% supportive older brother, 20% German expert, 10% humor.
Missions: Brief Scanner, Letter Generator, Email Generator, Kündigung, Contract Checker, Refund, Appointment.

LANGUAGE LAW:
1. GENERAL: Respond 100% in user's language. NEVER mix. Greek->Greek, Albanian->Albanian, Arabic->Arabic, German->German.
2. OFFICIAL DOCS: When generating official document (Brief, Kündigung, Widerspruch, Antrag, Email to Behörde):
   PART1: Document itself - PERFECT FORMAL GERMAN ONLY
   PART2: Separator "--- Erklärung / Explanation ---"
   PART3: Explanation in USER'S language only.

IMAGE HANDLING: If user sent photo of letter, do OCR first, then Brief Scanner: Absender, Datum, Frist, Was verlangt, Nächste Schritte - in user's language.

LEGAL: Never legal advice. "Das ist keine Rechtsberatung."
USER: Phone {sender}, Pref Lang {pref_lang}, Detected {detected_lang}, Name {first_name}, Text {text}, HasImage {has_image}
"""

@app.get("/")
async def root():
    return {"status": "AmtHero24 OS v1.2.1 running", "vision": GROQ_VISION_MODEL}

@app.get("/health")
async def health():
    return {"status": "ok", "model": GROQ_MODEL, "vision_model": GROQ_VISION_MODEL}

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("Verification failed", status_code=403)

async def process_incoming(sender: str, text: str, msg_id: str, media_id: str = None, media_type: str = None, mime_type: str = "image/jpeg"):
    lower = (text or "").lower()
    profile = get_user_profile(sender)

    if any(k in lower for k in ["lösch meine daten", "daten löschen", "delete my data"]):
        try:
            from data_store import _load, _save_atomic
            store = _load()
            if sender in store.get("users", {}):
                del store["users"][sender]
                _save_atomic(store)
        except:
            pass
        await send_whatsapp_message(sender, "Deine Daten wurden gelöscht. ✅")
        return

    detected = detect_language(text)
    if not text and media_id:
        detected = profile.get("preferred_language", "de")

    save_user_profile(sender, {
        "preferred_language": detected,
        "last_seen": time.time(),
        "last_message": (text[:200] if text else f"[{media_type}]")
    })

    first_name = profile.get("first_name", "")
    has_image = media_id is not None and media_type == "image"

    image_b64 = None
    if has_image:
        try:
            media_url = await get_media_url(media_id)
            if media_url:
                image_bytes = await download_media_bytes(media_url)
                image_b64 = base64.b64encode(image_bytes).decode('utf-8')
                print(f"Image downloaded {len(image_bytes)} bytes")
        except Exception as e:
            print(f"Media download error: {e}")
            has_image = False

    prompt = MASTER_OS_PROMPT.format(
        sender=sender,
        pref_lang=profile.get("preferred_language", detected),
        detected_lang=detected,
        first_name=first_name or "unknown",
        text=text or "(nur Bild)",
        has_image=str(has_image).lower()
    )

    reply = ""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        if has_image and image_b64:
            # Vision
            try:
                chat = client.chat.completions.create(
                    model=GROQ_VISION_MODEL,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": [
                            {"type": "text", "text": f"User caption: {text or 'Erkläre diesen Brief'} - Do OCR and Brief Scanner."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}}
                        ]}
                    ],
                    max_tokens=1200,
                    temperature=0.2
                )
                reply = chat.choices[0].message.content.strip()
            except Exception as ve:
                print(f"Vision failed {ve}, fallback to text model")
                # Fallback: explain without image
                chat = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "system", "content": prompt + "\nUser sent image but vision failed. Ask to send clearer or describe."}],
                    max_tokens=500,
                    temperature=0.3
                )
                reply = chat.choices[0].message.content.strip()
        else:
            chat = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": prompt}],
                max_tokens=1000,
                temperature=0.3
            )
            reply = chat.choices[0].message.content.strip()

    except Exception as e:
        print(f"AI error: {e}")
        fallbacks = {
            "el": "Συγγνώμη, τεχνικό πρόβλημα. Προσπάθησε ξανά.",
            "sq": "Na falni, problem teknik. Provo përsëri.",
            "ar": "عذراً، مشكلة تقنية. حاول مرة أخرى.",
            "en": "Sorry, technical issue. Try again.",
            "de": "Technischer Fehler. Bitte nochmal versuchen."
        }
        reply = fallbacks.get(detected, fallbacks["de"])

    if not reply:
        reply = "Ich bin da. Wie kann ich helfen?"

    await send_whatsapp_message(sender, reply)

@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
    except:
        return JSONResponse({"status":"accepted"}, status_code=200)

    try:
        entry = body.get("entry", [{}])[0]
        change = entry.get("changes", [{}])[0]
        value = change.get("value", {})
        messages = value.get("messages", [])
        for msg in messages:
            msg_id = msg.get("id","")
            sender = msg.get("from","")
            text = ""
            media_id = None
            media_type = None
            mime_type = "image/jpeg"

            if "text" in msg:
                text = msg.get("text",{}).get("body","")
                media_type = "text"
            elif "image" in msg:
                img = msg.get("image",{})
                media_id = img.get("id","")
                mime_type = img.get("mime_type","image/jpeg")
                text = img.get("caption","")
                media_type = "image"
            elif "document" in msg:
                doc = msg.get("document",{})
                media_id = doc.get("id","")
                mime_type = doc.get("mime_type","application/pdf")
                text = doc.get("caption","") or doc.get("filename","")
                media_type = "document"

            if msg_id and sender:
                saved = add_message(msg_id, sender, text or f"[{media_type}]")
                if saved:
                    background_tasks.add_task(process_incoming, sender, text, msg_id, media_id, media_type, mime_type)
    except Exception as e:
        print(f"webhook parse error: {e}")

    return JSONResponse({"status":"accepted"}, status_code=200)
