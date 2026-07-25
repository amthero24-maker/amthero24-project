from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import os
from config import VERIFY_TOKEN, GROQ_MODEL, GROQ_API_KEY
from data_store import add_message
from whatsapp import send_whatsapp_message
app = FastAPI(title="AmtHero24 OS", version="1.1")
@app.get("/health")
async def health():
    return {"status": "ok", "model": GROQ_MODEL, "phone_id": os.getenv("PHONE_NUMBER_ID","1264010770128749")}
@app.get("/webhook")
async def verify_webhook(hub_mode: str = Query(None, alias="hub.mode"), hub_verify_token: str = Query(None, alias="hub.verify_token"), hub_challenge: str = Query(None, alias="hub.challenge")):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("Verification failed", status_code=403)
async def process_incoming(sender: str, text: str, msg_id: str):
    prompt = f"Du bist AmtHero24 - User {sender}: {text}"
    reply = ""
    try:
        if GROQ_API_KEY:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            chat = client.chat.completions.create(model=GROQ_MODEL, messages=[{"role":"user","content": prompt}], max_tokens=400)
            reply = chat.choices[0].message.content.strip()
        else:
            reply = f"Hi: {text}"
    except Exception as e:
        print(e)
        reply = "Fehler, nochmal bitte."
    await send_whatsapp_message(sender, reply)
@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    try: body = await request.json()
    except: return JSONResponse({"status":"accepted"}, status_code=200)
    try:
        entry = body.get("entry", [{}])[0]
        change = entry.get("changes", [{}])[0]
        value = change.get("value", {})
        for msg in value.get("messages", []):
            msg_id = msg.get("id","")
            sender = msg.get("from","")
            text = msg.get("text",{}).get("body","") or ""
            if msg_id and sender and add_message(msg_id, sender, text):
                background_tasks.add_task(process_incoming, sender, text, msg_id)
    except Exception as e:
        print(e)
    return JSONResponse({"status":"accepted"}, status_code=200)
