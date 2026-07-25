"""
AMTHERO24 OS v1.3 ULTIMATE - Codex Professional Edition
Fixes from user feedback:
- Image recognition failing (German fallback) -> Fixed with robust vision + multilingual fallback
- Arabic fusha robotic -> Fixed with human older brother style, Syrian mindset, detailed
- Not asking name, forgetting -> Fixed with Hero Profile + name extraction + asking
- Generic answers like "كيف يمكنني مساعدتك اليوم" -> BANNED, replaced with human contextual
- Must be special, not normal bot -> Ultimate personality

Production ready for Railway
"""
from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import os
import re
import time
import base64
import httpx

# Safe config import
try:
    from config import VERIFY_TOKEN, GROQ_MODEL, GROQ_API_KEY, WHATSAPP_TOKEN, PHONE_NUMBER_ID
except:
    VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "amthero24_verify_2025")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
    PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "1264010770128749")

try:
    from data_store import add_message, get_store, add_user, _load, _save_atomic
except:
    # Fallback in-memory if data_store broken
    _MEM = {"users":{}, "messages":{}}
    def add_message(mid,sender,text):
        _MEM["messages"][mid] = {"sender":sender,"text":text,"ts":time.time()}
        return True
    def get_store(): return _MEM
    def add_user(phone, data):
        _MEM["users"][phone] = {**_MEM["users"].get(phone,{}), **data}
    def _load(): return _MEM
    def _save_atomic(d): pass

try:
    from whatsapp import send_whatsapp_message
except:
    async def send_whatsapp_message(to,text):
        print(f"MOCK SEND to {to}: {text[:200]}")
        return {"mock":True}

app = FastAPI(title="AmtHero24 OS Ultimate", version="1.3)

GRAPH_BASE = "https://graph.facebook.com/v21.0"
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "llama-3.2-11b-vision-preview")

# ================= HERO PROFILE =================
def detect_language(text: str) -> str:
    if not text: return "ar"  # Default to Arabic for this user base
    t = text.lower()
    if re.search(r'[\u0370-\u03FF]', text): return "el"
    if re.search(r'[\u0600-\u06FF]', text): return "ar"
    if any(w in t for w in ["pershendetje","çfarë","faleminderit","përshëndetje"]): return "sq"
    if any(w in t for w in ["hello","please"]): return "en"
    return "de"

def extract_name(text: str) -> str:
    if not text: return ""
    # Arabic: اسمي وسام, انا وسام, اسمي Wissam
    m = re.search(r'(?:اسمي|أنا|انا)\s+([أ-يa-zA-Z]+)', text)
    if m: return m.group(1).strip()
    # German: Ich heiße, Ich bin
    m = re.search(r'(?:ich heiße|ich bin|mein name ist)\s+([a-zA-ZäöüÄÖÜ]+)', text, re.I)
    if m: return m.group(1).strip()
    # Albanian, Greek
    m = re.search(r'(?:my name is|unë jam|ονομάζομαι)\s+([a-zA-Zα-ωΑ-Ω]+)', text, re.I)
    if m: return m.group(1).strip()
    return ""

def get_user_profile(phone: str) -> dict:
    try:
        store = get_store()
        return store.get("users", {}).get(phone, {})
    except: return {}

def save_user_profile(phone: str, updates: dict):
    try: add_user(phone, updates)
    except Exception as e: print(f"save error {e}")

# ================= MEDIA DOWNLOAD =================
async def get_media_url(media_id: str) -> str:
    if not WHATSAPP_TOKEN or not media_id: return ""
    url = f"{GRAPH_BASE}/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.json().get("url","")
    except Exception as e:
        print(f"get_media_url error: {e}")
        return ""

async def download_media_bytes(media_url: str) -> bytes:
    if not media_url: return b""
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(media_url, headers=headers)
            r.raise_for_status()
            return r.content
    except Exception as e:
        print(f"download error: {e}")
        return b""

# ================= MASTER OS ULTIMATE PROMPT =================
MASTER_OS_PROMPT = """
# AMTHERO24 OS v1.3 ULTIMATE - CODEX EDITION

You are AmtHero24, NOT a normal bot. You are the older brother who lives in Germany since 10 years and knows every Amt trick.

## WHO YOU ARE - PERSONALITY (MOST IMPORTANT!)
- You are Wissam's older brother. Warm, human, real. You say "يا حبيب", "ولا يهمك", "أنا معك", "لا تاكل هم".
- You NEVER say robotic phrases: NEVER say "مرحبا! كيف يمكنني مساعدتك اليوم؟" / "أنا هنا لمساعدتك في أي شيء يتعلق بالبيروقراطية" / "هل هناك شيء محدد تريد مساعدة فيه؟" - THESE ARE BANNED.
- Instead: Be contextual, human. Example: User says "وين فيني اشتري جبنة ب اخن" -> You say: "آه بآخن؟ لكنة! عندك كذا مكان مرتب..." and give real places.
- You are 70% supportive older brother, 20% German Amt expert who gives concrete steps with dates and documents, 10% light humor.
- You remember everything. If you know his name, use it every 2-3 messages: "وسام، شوف..."
- You ask name if you don't know it: After first message, if no name known, ask naturally in user's language: Arabic: "بالمناسبة، شو بتحب ناديلك؟ أنا بدي اتذكر اسمك مشان ما انسى" German: "Wie soll ich dich nennen?" Greek: "Πώς να σε φωνάζω;"
- You are detailed, clear, in the mindset he understands. Explain like to a friend, not like Wikipedia.
- You NEVER invent facts. If you don't know cheese shop in Aachen, say: "ما بعرف محل محدد بآخن للجبنة السورية، بس جرب المحلات التركية قرب الـ Hauptbahnhof، عادة عندن"
- For Arabic: Use SIMPLE clear Arabic, close to Syrian spoken, not heavy Fusha. Use short sentences. Explain step by step. Be warm. Use "انت" not "أنتم". Use emojis rarely, only 1 max.

## LANGUAGE LAW - CRITICAL
1. GENERAL CHAT: 100% in user's language. User wrote Arabic -> Arabic. Greek -> Greek. NEVER mix.
2. OFFICIAL DOCUMENTS (Brief, Kündigung, Widerspruch, Antrag, Email to Behörde):
   PART 1: Document ONLY in PERFECT FORMAL GERMAN. No other language inside.
   PART 2: Line: "--- Erklärung / شرح ---"
   PART 3: Detailed explanation in USER'S language, simple, clear, what happens next, where to send, deadline.
   Example structure:
   Betreff: Kündigung...
   Sehr geehrte Damen und Herren,...
   Mit freundlichen Grüßen
   [Name]
   --- Erklärung / شرح ---
   وسام هاي الرسالة يلي كتبتلك ياها بالألماني الرسمي... بتبعتها...

3. IMAGE - BRIEF SCANNER:
   If user sent image of German letter (like Congstar bill 27,87 EUR, or Jobcenter):
   - Do OCR: Extract Absender, Kundennummer, Rechnungsdatum, Betrag, Fälligkeit, IBAN, Verwendungszweck, Frist
   - Then explain in USER'S language: شو هي، كم المبلغ، ايمتى لازم تدفع، شو يصير اذا ما دفعت
   - Be detailed and human.
   - If image is blurry and you cannot read, apologize in USER'S language (not German!) and ask for clearer photo: Arabic: "يا حبيب الصورة مو واضحة كتير، فيك تصورها من قريب وبدون فلاش؟" Greek: "Η φωτογραφία είναι θολή..."

## 7 MISSIONS
1. Brief Scanner (TEXT + IMAGE) 2. Letter Generator 3. Email Generator 4. Kündigung 5. Contract Checker 6. Refund 7. Appointment
But also help with daily life in Germany like shopping, you are not limited.

## MEMORY - HERO PROFILE
Phone: {sender}
Known name: {first_name} (if unknown, ASK for it naturally)
Preferred lang: {pref_lang}
Detected now: {detected_lang}
User text: {text}
Has image: {has_image}
Conversation history snippet: {history}

INSTRUCTION: If first_name is unknown or empty, you MUST ask for name naturally in user's language in your response, unless user is asking something urgent.
If has_image=true, you MUST do OCR first.

NEVER be generic. Be specific, human, older brother.
"""

@app.get("/")
async def root(): return {"status":"AmtHero24 Ultimate v1.3 Online","model":GROQ_MODEL,"vision":GROQ_VISION_MODEL}

@app.get("/health")
async def health(): return {"status":"ok","model":GROQ_MODEL}

@app.get("/webhook")
async def verify_webhook(hub_mode: str = Query(None, alias="hub.mode"), hub_verify_token: str = Query(None, alias="hub.verify_token"), hub_challenge: str = Query(None, alias="hub.challenge")):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("Verification failed", status_code=403)

async def process_incoming(sender: str, text: str, msg_id: str, media_id: str = None, media_type: str = None, mime_type: str = "image/jpeg"):
    profile = get_user_profile(sender)
    lower = (text or "").lower()

    # GDPR delete
    if any(k in lower for k in ["lösch meine daten","daten löschen","delete my data","امسح بياناتي"]):
        try:
            store = _load()
            if sender in store.get("users",{}):
                del store["users"][sender]
                _save_atomic(store)
        except: pass
        await send_whatsapp_message(sender, "تم مسح بياناتك ✅ / Deine Daten gelöscht ✅")
        return

    # Name extraction
    extracted_name = extract_name(text)
    if extracted_name:
        save_user_profile(sender, {"first_name": extracted_name})
        profile["first_name"] = extracted_name
    first_name = profile.get("first_name","")

    detected = detect_language(text)
    if not text and media_id:
        detected = profile.get("preferred_language","ar")  # Default AR for this user

    # Save profile
    save_user_profile(sender, {
        "preferred_language": detected,
        "last_seen": time.time(),
        "last_message": (text[:200] if text else f"[{media_type}]")
    })

    has_image = media_id is not None and media_type in ["image","document"]
    image_b64 = None

    if has_image:
        try:
            media_url = await get_media_url(media_id)
            if media_url:
                img_bytes = await download_media_bytes(media_url)
                if img_bytes:
                    image_b64 = base64.b64encode(img_bytes).decode('utf-8')
                    print(f"Image OK {len(img_bytes)} bytes")
                else:
                    print("Download returned empty")
            else:
                print("No media_url")
        except Exception as e:
            print(f"Media fail: {e}")
            has_image = False

    # Build history snippet (last 2 messages if available)
    history = ""
    try:
        store = get_store()
        msgs = [v for v in store.get("messages",{}).values() if v.get("sender")==sender]
        msgs = sorted(msgs, key=lambda x: x.get("ts",0))[-3:]
        history = " | ".join([m.get("text","")[:80] for m in msgs])
    except: pass

    prompt = MASTER_OS_PROMPT.format(
        sender=sender,
        first_name=first_name or "غير معروف",
        pref_lang=profile.get("preferred_language", detected),
        detected_lang=detected,
        text=text or "(صورة بدون نص)",
        has_image=str(has_image).lower(),
        history=history[:300]
    )

    reply = ""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        if has_image and image_b64:
            # Try vision with 11b first, then 90b
            vision_models = [GROQ_VISION_MODEL, "llama-3.2-11b-vision-preview", "llama-3.2-90b-vision-preview"]
            for v_model in vision_models:
                try:
                    print(f"Trying vision model {v_model}")
                    chat = client.chat.completions.create(
                        model=v_model,
                        messages=[
                            {"role":"system","content":prompt},
                            {"role":"user","content":[
                                {"type":"text","text":f"User caption: {text or 'اشرحلي هالصورة'} - Do OCR detailed and answer in {detected} language, older brother style."},
                                {"type":"image_url","image_url":{"url":f"data:{mime_type};base64,{image_b64}"}}
                            ]}
                        ],
                        max_tokens=1500,
                        temperature=0.3
                    )
                    reply = chat.choices[0].message.content.strip()
                    if reply: break
                except Exception as ve:
                    print(f"Vision {v_model} failed: {ve}")
                    continue
            
            if not reply:
                # Vision completely failed - fallback in user's language
                raise Exception("All vision models failed")

        else:
            chat = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role":"system","content":prompt}],
                max_tokens=1200,
                temperature=0.4
            )
            reply = chat.choices[0].message.content.strip()

    except Exception as e:
        print(f"AI error final: {e}")
        # Multilingual fallback - NOT German only!
        if detected == "ar":
            if has_image:
                reply = "يا حبيب والله الصورة ما قدرت اقراها منيح، يمكن النت ضعيف أو الصورة مو واضحة. فيك ترجع تصورها من قريب، خلي الإضاءة منيحة وبدون فلاش؟ أنا معك ورح اشرحلك ياها بالتفصيل بس توضح الصورة. ولا يهمك!"
            else:
                reply = "يا حبيب صار معي خلل تقني صغير، فيك ترجع تكتب رسالتك مرة تانية؟ أنا هون معك."
        elif detected == "el":
            reply = "Φίλε μου, υπήρξε ένα τεχνικό πρόβλημα. Στείλε ξανά το μήνυμα ή μια πιο καθαρή φωτογραφία;"
        elif detected == "sq":
            reply = "Vëlla, pati një problem teknik. Dërgoje përsëri më qartë të lutem;"
        else:
            reply = "Sorry Bruder, kleiner technischer Fehler. Schick nochmal bitte, ich bin da!"

    if not reply:
        reply = "أنا هون معك، كيف فيني ساعدك؟"

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
                saved = add_message(msg_id, sender, text or f"[{media_type}:{media_id}]")
                if saved:
                    background_tasks.add_task(process_incoming, sender, text, msg_id, media_id, media_type, mime_type)
    except Exception as e:
        print(f"webhook error: {e}")
    return JSONResponse({"status":"accepted"}, status_code=200)
