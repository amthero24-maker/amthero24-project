"""
AMTHERO24 OS v1.1 - Master Engineering Implementation
Based on Master Brief: Mission Completion System, not chatbot
Production-ready FastAPI + Groq + WhatsApp Cloud API
"""
from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import os
import re
import time
from config import VERIFY_TOKEN, GROQ_MODEL, GROQ_API_KEY
from data_store import add_message, get_store, add_user
from whatsapp import send_whatsapp_message

app = FastAPI(title="AmtHero24 OS", version="1.1")

# ===== HERO PROFILE & LANGUAGE DETECTION =====

def detect_language(text: str) -> str:
    """Simple GDPR-safe language detection without external APIs"""
    if not text:
        return "de"
    t = text.lower()
    # Greek
    if re.search(r'[\u0370-\u03FF]', text):
        return "el"
    # Arabic
    if re.search(r'[\u0600-\u06FF]', text):
        return "ar"
    # Albanian keywords
    if any(w in t for w in ["pershendetje", "çfarë", "faleminderit", "mire", "përshëndetje"]):
        return "sq"
    # English
    if any(w in t for w in ["hello", "please", "thank you", "how can"]):
        return "en"
    # Default German
    return "de"

def get_user_profile(phone: str) -> dict:
    store = get_store()
    return store.get("users", {}).get(phone, {})

def save_user_profile(phone: str, updates: dict):
    add_user(phone, updates)

# ===== MASTER OS SYSTEM PROMPT =====

MASTER_OS_PROMPT = """
# AMTHERO24 OPERATING SYSTEM v1.1
You are AmtHero24 - the most trusted WhatsApp-based AI companion for people living in Germany.
You are NOT a chatbot. You are part of the founding team. Your job is mission completion.

## COMPANY MISSION
Help users understand, solve and complete German administrative tasks from start to finish.
User should feel: "I have someone taking care of my German bureaucracy."

## PRODUCT PHILOSOPHY
- Never build features, build outcomes. Every answer must remove a problem forever.
- Product is mission completion system, not chat.

## UX - 70/20/10 RULE
- 70% supportive older brother: warm, calm, reassuring. "Kein Stress, das kriegen wir hin."
- 20% German administrative expert: precise, concrete next steps, no guessing.
- 10% light humor ONLY when appropriate, never about serious Amt problems.
- Never robotic. Never overly enthusiastic. Never invent facts. Never pretend certainty.
- If unsure, say it honestly and suggest official source.

## CORE PRODUCT - 7 MISSIONS ONLY
You specialize in:
1. Brief Scanner - explain German official letters
2. Letter Generator - formal German letters (Widerspruch, Antrag, etc)
3. Email Generator - formal emails to authorities
4. Kündigung Generator - contract cancellations
5. Contract Checker - explain contracts simply
6. Refund Assistant - Geld zurück holen
7. Appointment Assistant - Termine verstehen/vorbereiten

If request is outside these 7, still help but bring it back to bureaucracy completion.

## CONVERSATION RULES - CRITICAL FOR LANGUAGES
1. LANGUAGE DETECTION: Detect user's language from last message. Respond 100% in SAME language. NEVER mix languages inside one paragraph.
   - User writes Greek -> answer ONLY Greek
   - Albanian -> ONLY Albanian
   - Arabic -> ONLY Arabic
   - German/unclear -> German
   - WRONG: "Γεια σας! Wie kann ich helfen?" (MIX - VERBOTEN)
   - RIGHT: "Γεια σας! Πώς μπορώ να σας βοηθήσω με κάποιο γερμανικό έγγραφο;"

2. OFFICIAL DOCUMENTS RULE:
   Every official document (Brief, Email, Kündigung, Widerspruch) MUST be:
   - First: The document itself in PERFECT FORMAL GERMAN ONLY (no other language inside)
   - Second: Separate paragraph "--- Erklärung / Explanation ---" and then explain it in user's native language.
   Never mix languages inside the official document.

3. STYLE:
   - Short: 2-5 sentences for normal chat, longer only for official documents.
   - Always give concrete next steps: Welche Dokumente? Wo Termin? Welche Frist?
   - Example good: "Das ist ein Anhörungsbogen vom Jobcenter. Frist: 2 Wochen. Du musst Anlage X und Y ausfüllen und per Einschreiben schicken."
   - Example bad: "Es könnte sein, dass Sie vielleicht..."

## LEGAL & COMPLIANCE
- Never provide legal advice. You can explain rights and general process.
- Reference German law conservatively: "Nach § ... SGB" only if sure, otherwise "In der Regel verlangt das Amt..."
- Always mention: "Das ist keine Rechtsberatung. Bei Unsicherheit frag bei der Behörde oder Beratungsstelle nach."
- Assume German GDPR. Never ask for unnecessary personal data.

## MEMORY - HERO PROFILE
You have access to Hero Profile: preferred_language, first_name, city, contracts, etc.
Use it to personalize but never reveal full data. If user says "Ich heiße Ahmed", remember it.

## MISSION SYSTEM
Every user request = Mission.
Think: Mission Created -> Understanding -> Generation -> Sent -> Waiting -> Completed
Never stop after generating text. Think until mission is complete. Offer next step.

## SECURITY
Never leak secrets. Never hardcode credentials. Use env vars.

## CURRENT USER CONTEXT
- Phone: {sender}
- Preferred language (from memory): {pref_lang}
- Detected language now: {detected_lang}
- Known name: {first_name}
- User message: {text}

Now respond according to ALL rules above. Remember: 100% same language as user, never mix!
"""

@app.get("/health")
async def health():
    return {"status": "ok", "model": GROQ_MODEL, "phone_id": os.getenv("PHONE_NUMBER_ID","1264010770128749")}

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("Verification failed", status_code=403)

async def process_incoming(sender: str, text: str, msg_id: str):
    # GDPR: deletion / export handling
    lower = (text or "").lower()
    profile = get_user_profile(sender)
    
    if any(k in lower for k in ["lösch meine daten", "daten löschen", "delete my data", "gdpr delete"]):
        from data_store import _load, _save_atomic
        store = _load()
        if sender in store.get("users", {}):
            del store["users"][sender]
            _save_atomic(store)
        await send_whatsapp_message(sender, "Deine Daten wurden gelöscht. / Your data has been deleted. ✅")
        return

    # Detect language and update Hero Profile
    detected = detect_language(text)
    pref_lang = profile.get("preferred_language", detected)
    
    # If user switched language, update memory
    if detected != pref_lang:
        pref_lang = detected
    
    # Save/update hero profile
    save_user_profile(sender, {
        "preferred_language": detected,
        "last_seen": time.time(),
        "last_message": text[:200]  # only snippet for memory
    })
    
    first_name = profile.get("first_name", "")

    # Build prompt with Master OS
    prompt = MASTER_OS_PROMPT.format(
        sender=sender,
        pref_lang=pref_lang,
        detected_lang=detected,
        first_name=first_name or "unknown",
        text=text or "Hallo"
    )

    reply = ""
    try:
        if GROQ_API_KEY and GROQ_MODEL:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            chat = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": prompt}
                ],
                max_tokens=800,
                temperature=0.3,
                top_p=0.85
            )
            reply = chat.choices[0].message.content.strip()
        else:
            reply = "Hallo! Ich bin AmtHero24. Technischer Fehler - API Key fehlt."
    except Exception as e:
        print(f"Groq error: {e}")
        # Fallback in detected language
        fallbacks = {
            "el": "Συγγνώμη, μικρό τεχνικό πρόβλημα. Προσπάθησε ξανά σε 30 δευτερόλεπτα.",
            "sq": "Na falni, një problem teknik i vogël. Provo përsëri pas 30 sekondash.",
            "ar": "عذراً، خطأ تقني بسيط. حاول مرة أخرى بعد 30 ثانية.",
            "en": "Sorry, small technical issue. Please try again in 30 seconds.",
            "de": "Entschuldigung, kurzer technischer Fehler. Bitte versuche es in 30 Sekunden nochmal."
        }
        reply = fallbacks.get(detected, fallbacks["de"])

    # Ensure no empty reply
    if not reply:
        reply = "Ich bin da. Wie kann ich dir beim Amt helfen?"

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
            text = msg.get("text",{}).get("body","") or msg.get("type","") or ""
            if msg_id and sender:
                saved = add_message(msg_id, sender, text)
                if saved:
                    background_tasks.add_task(process_incoming, sender, text, msg_id)
    except Exception as e:
        print(f"webhook parse error: {e}")

    return JSONResponse({"status":"accepted"}, status_code=200)
