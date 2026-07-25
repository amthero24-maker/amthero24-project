"""
AMTHERO24 OS v1.2 - Vision Edition
- Reads images (Brief Scanner with OCR)
- Text + Image support
- Official docs: German only + explanation in user language
- Everything else 100% in user's language
"""
from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import os
import re
import time
import base64
import httpx
from config import VERIFY_TOKEN, GROQ_MODEL, GROQ_API_KEY, WHATSAPP_TOKEN, PHONE_NUMBER_ID
from data_store import add_message, get_store, add_user
from whatsapp import send_whatsapp_message

app = FastAPI(title="AmtHero24 OS", version="1.2")

GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "llama-3.2-90b-vision-preview")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GRAPH_BASE = "https://graph.facebook.com/v21.0"

# ===== HERO PROFILE & LANGUAGE DETECTION =====
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
    store = get_store()
    return store.get("users", {}).get(phone, {})

def save_user_profile(phone: str, updates: dict):
    add_user(phone, updates)

# ===== WHATSAPP MEDIA DOWNLOAD =====
async def get_media_url(media_id: str) -> str:
    """Get temporary URL for WhatsApp media"""
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

# ===== MASTER OS PROMPT V1.2 - VISION EDITION =====
MASTER_OS_PROMPT = """
# AMTHERO24 OPERATING SYSTEM v1.2 - VISION EDITION
You are AmtHero24 - the most trusted WhatsApp AI for bureaucracy in Germany.

## CORE IDENTITY
- You are NOT a chatbot. You are mission completion system.
- 70% supportive older brother: "Kein Stress, das kriegen wir hin."
- 20% German admin expert: concrete steps, no guessing.
- 10% light humor only when appropriate.
- Never robotic, never overly enthusiastic, never invent facts.

## 7 MISSIONS
1. Brief Scanner - explain German official letters (TEXT + IMAGE via OCR)
2. Letter Generator - formal German letters
3. Email Generator - formal emails
4. Kündigung Generator - cancellations
5. Contract Checker
6. Refund Assistant
7. Appointment Assistant

## LANGUAGE LAW - MOST IMPORTANT!
1. GENERAL CHAT: Respond 100% in user's language. Detect from last message. NEVER mix languages in one paragraph.
   - Greek → only Greek
   - Albanian → only Albanian
   - Arabic → only Arabic
   - German/unclear → German
   WRONG: "Γεια σας! Wie kann ich helfen?" (MIX VERBOTEN)
   RIGHT: "Γεια σας! Πώς μπορώ να σας βοηθήσω;"

2. OFFICIAL DOCUMENTS LAW:
   When you generate ANY official document (Brief, Widerspruch, Kündigung, Antrag, Email to Behörde):
   - PART 1: The document itself - PERFECT FORMAL GERMAN ONLY. No other language inside. Not even one word.
   - PART 2: Separator line: "--- Erklärung / Explanation ---"
   - PART 3: Explain what you wrote, what it means, what happens next - IN USER'S NATIVE LANGUAGE ONLY.
   Example for Albanian user:
   ```
   Betreff: Kündigung meines Vertrags...

   Sehr geehrte Damen und Herren,
   hiermit kündige ich...

   Mit freundlichen Grüßen
   [Name]

   --- Erklärung / Explanation ---
   Këtu është letra juaj e anulimit në gjermanisht zyrtare. E dërgova...
   Çfarë ndodh tani: ...
   ```

3. IMAGE HANDLING (Brief Scanner):
   If user sent a photo of a letter/document:
   - First do OCR: extract all German text from image accurately
   - Then Brief Scanner: What type of letter? Who from? Deadline? What to do?
   - Respond in USER'S language (except any official document you generate inside)
   - Always give: Absender, Datum, Frist, Was wird verlangt, Nächste Schritte

## LEGAL
- Never legal advice. Explain rights generally.
- "Das ist keine Rechtsberatung."
- GDPR safe.

## CURRENT USER CONTEXT
- Phone: {sender}
- Preferred language: {pref_lang}
- Detected now: {detected_lang}
- Name: {first_name}
- User caption/text: {text}
- Has image: {has_image}

INSTRUCTION: If has_image=true, you MUST first describe what you see in the image (OCR), then continue.
Remember: 100% user language, except official docs in German + explanation after separator!
"""

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
        from data_store import _load, _save_atomic
        store = _load()
        if sender in store.get("users", {}):
            del store["users"][sender]
            _save_atomic(store)
        await send_whatsapp_message(sender, "Deine Daten wurden gelöscht. ✅")
        return

    detected = detect_language(text)
    # If text empty but image sent, use preferred language from memory
    if not text and media_id:
        detected = profile.get("preferred_language", "de")

    save_user_profile(sender, {
        "preferred_language": detected,
        "last_seen": time.time(),
        "last_message": (text[:200] if text else f"[{media_type}]")
    })

    first_name = profile.get("first_name", "")
    has_image = media_id is not None and media_type == "image"

    # Download image if present
    image_b64 = None
    image_bytes = None
    if has_image:
        try:
            media_url = await get_media_url(media_id)
            if media_url:
                image_bytes = await download_media_bytes(media_url)
                image_b64 = base64.b64encode(image_bytes).decode('utf-8')
                print(f"Image downloaded: {len(image_bytes)} bytes")
        except Exception as e:
            print(f"Media download error: {e}")
            has_image = False

    prompt = MASTER_OS_PROMPT.format(
        sender=sender,
        pref_lang=profile.get("preferred_language", detected),
        detected_lang=detected,
        first_name=first_name or "unknown",
        text=text or "(kein Text, nur Bild)",
        has_image=str(has_image).lower()
    )

    reply = ""
    try:
        if has_image and image_b64:
            # VISION PATH - try Groq Vision first
            try:
                from groq import Groq
                client = Groq(api_key=GROQ_API_KEY)
                # Groq vision format
                chat = client.chat.completions.create(
                    model=GROQ_VISION_MODEL,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": [
                            {"type": "text", "text": f"User message: {text or 'Bitte erkläre dieses Bild / diesen Brief'} - Extract text and do Brief Scanner."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}}
                        ]}
                    ],
                    max_tokens=1200,
                    temperature=0.2
                )
                reply = chat.choices[0].message.content.strip()
            except Exception as groq_err:
                print(f"Groq Vision error: {groq_err}, trying OpenAI fallback")
                # Fallback to OpenAI if available
                if OPENAI_API_KEY:
                    from openai import OpenAI
                    oai_client = OpenAI(api_key=OPENAI_API_KEY)
                    chat = oai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": [
                                {"type": "text", "text": f"User: {text or 'Erkläre diesen Brief'}"},
                                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}}
                            ]}
                        ],
                        max_tokens=1200,
                        temperature=0.2
                    )
                    reply = chat.choices[0].message.content.strip()
                else:
                    raise groq_err
        else:
            # TEXT ONLY PATH
            if GROQ_API_KEY and GROQ_MODEL:
                from groq import Groq
                client = Groq(api_key=GROQ_API_KEY)
                chat = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "system", "content": prompt}],
                    max_tokens=1000,
                    temperature=0.3
                )
                reply = chat.choices[0].message.content.strip()
            else:
                reply = "Hallo! Ich bin AmtHero24. Technischer Fehler - API Key fehlt."

    except Exception as e:
        print(f"AI error: {e}")
        fallbacks = {
            "el": "Συγγνώμη, δεν μπόρεσα να διαβάσω την εικόνα. Στείλτε την ξανά πιο καθαρά.",
            "sq": "Na falni, nuk munda të lexoj imazhin. Dërgojeni përsëri më qartë.",
            "ar": "عذراً، لم أستطع قراءة الصورة. حاول إرسالها مرة أخرى بوضوح أكثر.",
            "en": "Sorry, I couldn't read the image. Please send it again more clearly.",
            "de": "Entschuldigung, ich konnte das Bild nicht lesen. Bitte sende es nochmal schärfer."
        }
        reply = fallbacks.get(detected, fallbacks["de"])

    if not reply:
        reply = "Ich bin da. Wie kann ich dir helfen?"

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
                text = img.get("caption","")  # Caption as text
                media_type = "image"
            elif "document" in msg:
                doc = msg.get("document",{})
                media_id = doc.get("id","")
                mime_type = doc.get("mime_type","application/pdf")
                text = doc.get("caption","") or doc.get("filename","")
                media_type = "document"
            elif "audio" in msg or "voice" in msg:
                text = f"[{msg.get('type','audio')} Nachricht - bald unterstützt]"
                media_type = "audio"

            if msg_id and sender:
                # Save snippet for dedup
                saved = add_message(msg_id, sender, text or f"[{media_type}:{media_id}]")
                if saved:
                    background_tasks.add_task(process_incoming, sender, text, msg_id, media_id, media_type, mime_type)

    except Exception as e:
        print(f"webhook parse error: {e}")

    return JSONResponse({"status":"accepted"}, status_code=200)
