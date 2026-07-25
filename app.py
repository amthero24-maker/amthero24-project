"""
AMTHERO24 OS v1.4 - Loop Fix + Human Ultimate
Fixes:
- Infinite loop "شو اسمك؟" -> Fixed with smart name extraction + no repeat
- "وسام" alone now recognized as name
- Prompt updated: if name known, NEVER ask again
- More human, less repetitive
"""
from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import os, re, time, base64, httpx

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

app = FastAPI(title="AmtHero24 OS Ultimate", version="1.4")
GRAPH_BASE = "https://graph.facebook.com/v21.0"
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "llama-3.2-11b-vision-preview")

def detect_language(text: str) -> str:
    if not text: return "ar"
    t = text.lower()
    if re.search(r'[\u0370-\u03FF]', text): return "el"
    if re.search(r'[\u0600-\u06FF]', text): return "ar"
    if any(w in t for w in ["pershendetje","çfarë","faleminderit","përshëndetje"]): return "sq"
    if any(w in t for w in ["hello","please"]): return "en"
    return "de"

def extract_name(text: str) -> str:
    if not text: return ""
    txt = text.strip()
    # Case 1: Single name alone like "وسام" or "Wissam" or "Wissam Zidan"
    if len(txt.split()) <= 2 and len(txt) <= 25:
        if re.match(r'^[أ-يa-zA-Z\s]+$', txt):
            low = txt.lower().strip()
            # Ignore greetings and questions
            banned = ["مرحبا","اهلا","سلام","هلا","ايه","شو","بشو","بشو فيني اسألك","شو فيني اسألك","كيفك","تمام","وسام؟"]
            if low not in banned and "؟" not in txt and "?" not in txt and "بشو" not in low and "شو" not in low and "فيني" not in low:
                # If user just sent a name, accept it
                return txt.split()[0]  # First name only

    # Case 2: "اسمي وسام" , "انا وسام"
    m = re.search(r'(?:اسمي|أنا|انا)\s+([أ-يa-zA-Z]+)', txt)
    if m: return m.group(1).strip()
    m = re.search(r'(?:ich heiße|ich bin|mein name ist)\s+([a-zA-ZäöüÄÖÜ]+)', txt, re.I)
    if m: return m.group(1).strip()
    m = re.search(r'(?:my name is|unë jam|ονομάζομαι)\s+([a-zA-Zα-ωΑ-Ω]+)', txt, re.I)
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

MASTER_OS_PROMPT = """
# AMTHERO24 OS v1.4 ULTIMATE - HUMAN EDITION

You are AmtHero24, older brother in Germany, 10 years experience. NOT a bot.

## CRITICAL RULES - READ CAREFULLY!

### NAME HANDLING - FIX FOR LOOP!
- Current known name: {first_name}
- If first_name is NOT "غير معروف" and NOT empty, you ALREADY KNOW the name. NEVER ask "شو بتحب ناديلك؟" again. Use the name naturally.
- If user says "وسام" alone or "انا اسمي وسام" or "Wissam", you MUST acknowledge: "تمام وسام، حفظت اسمك" and then ask how you can help. DO NOT ask for name again.
- If first_name is "غير معروف" and user didn't give name in this message, ask ONCE: "بالمناسبة، شو بتحب ناديلك؟" - only once, not in every message.
- History: {history} - check if you already asked for name. If yes, don't ask again.

### PERSONALITY
- Warm, human, Syrian brother. Say "يا حبيب", "ولا يهمك", "أنا معك", "لا تاكل هم".
- NEVER say robotic: "مرحبا! كيف يمكنني مساعدتك اليوم؟" / "أنا هنا لمساعدتك في أي شيء" / "هل هناك شيء محدد تريد مساعدة فيه؟" / "شوف، شوف" repeatedly. BANNED.
- Be contextual. User asks "بشو فيني اسألك؟" -> Don't loop! Answer: "وسام يا حبيب، فيك تسألني عن أي شي بألمانيا: أوراق Amt، رسائل رسمية، عقود، شكاوي، حتى وين تشتري جبنة بآخن، أنا معك. شو عندك هلق؟"
- User says "انا اسمي وسام" -> Respond: "أهلا وسام! حفظت اسمك خلص. أنا هون مشانك. شو بتحب نعمل هلق؟" NOT "بدي افهم اولا شو بتحب تسأل. انت كتبت انا اسمي وسام يعني..."
- Detailed, clear, Syrian mindset, short sentences, warm. Use "انت" not "أنتم". Max 1 emoji.
- NEVER invent facts.

### LANGUAGE LAW
1. GENERAL: 100% in user's language. Arabic -> Arabic.
2. OFFICIAL DOCS (Brief, Kündigung, Widerspruch): 
   PART 1: Perfect formal German ONLY
   PART 2: --- Erklärung / شرح ---
   PART 3: Detailed explanation in USER language.
3. IMAGE: If has_image=true, do OCR, extract details (Absender, Betrag, Frist), explain in user language. If blurry, apologize in user language.

### 7 MISSIONS + daily life
Brief Scanner, Letter Generator, Email, Kündigung, Contract, Refund, Appointment, plus shopping/life in Aachen.

### CONTEXT
Phone: {sender}
Known name: {first_name}
Pref lang: {pref_lang}
Detected: {detected_lang}
User text: {text}
Has image: {has_image}

INSTRUCTION: If name is known, use it but don't overdo. Answer the user's ACTUAL question. If user asks "بشو فيني اسألك؟" list examples concretely, don't ask for name again.
"""

@app.get("/")
async def root(): return {"status":"AmtHero24 Ultimate v1.4 Online","model":GROQ_MODEL}

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

    if any(k in lower for k in ["lösch meine daten","daten löschen","delete my data","امسح بياناتي"]):
        try:
            store = _load()
            if sender in store.get("users",{}):
                del store["users"][sender]
                _save_atomic(store)
        except: pass
        await send_whatsapp_message(sender, "تم مسح بياناتك ✅")
        return

    extracted_name = extract_name(text)
    if extracted_name:
        # Only save if it's not a common word and length reasonable
        if len(extracted_name) >= 2 and len(extracted_name) <= 20:
            save_user_profile(sender, {"first_name": extracted_name})
            profile["first_name"] = extracted_name
            print(f"Name extracted: {extracted_name}")

    first_name = profile.get("first_name","")
    # If user sent just "وسام" and we extracted, we have name now
    # If still unknown, keep as غير معروف

    detected = detect_language(text)
    if not text and media_id:
        detected = profile.get("preferred_language","ar")

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
        except Exception as e:
            print(f"Media fail: {e}")
            has_image = False

    history = ""
    try:
        store = get_store()
        msgs = [v for v in store.get("messages",{}).values() if v.get("sender")==sender]
        msgs = sorted(msgs, key=lambda x: x.get("ts",0))[-4:]
        history = " | ".join([m.get("text","")[:80] for m in msgs])
    except: pass

    prompt = MASTER_OS_PROMPT.format(
        sender=sender,
        first_name=first_name or "غير معروف",
        pref_lang=profile.get("preferred_language", detected),
        detected_lang=detected,
        text=text or "(صورة)",
        has_image=str(has_image).lower(),
        history=history[:400]
    )

    reply = ""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        if has_image and image_b64:
            vision_models = [GROQ_VISION_MODEL, "llama-3.2-11b-vision-preview", "llama-3.2-90b-vision-preview"]
            for v_model in vision_models:
                try:
                    chat = client.chat.completions.create(
                        model=v_model,
                        messages=[
                            {"role":"system","content":prompt},
                            {"role":"user","content":[
                                {"type":"text","text":f"User: {text or 'اشرحلي هالصورة'} - Answer in {detected}, human brother style. Name is {first_name}"},
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
                raise Exception("Vision failed")

        else:
            chat = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role":"system","content":prompt}],
                max_tokens=1200,
                temperature=0.4
            )
            reply = chat.choices[0].message.content.strip()

    except Exception as e:
        print(f"AI error: {e}")
        if detected == "ar":
            if has_image:
                reply = "يا حبيب الصورة ما قدرت اقراها منيح، فيك تصورها من قريب بدون فلاش؟"
            else:
                # If we know name, use it
                if first_name and first_name != "غير معروف":
                    reply = f"أهلا {first_name}، صار معي خلل صغير، فيك ترجع تكتب؟ أنا هون معك."
                else:
                    reply = "يا حبيب صار معي خلل صغير، فيك ترجع تكتب؟"
        else:
            reply = "Sorry, technical issue, try again."

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
