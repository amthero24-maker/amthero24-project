import httpx
from config import WHATSAPP_TOKEN, PHONE_NUMBER_ID
BASE='https://graph.facebook.com/v21.0'
async def send_whatsapp_message(to,text):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return {'error':'missing'}
    url=f'{BASE}/{PHONE_NUMBER_ID}/messages'
    headers={'Authorization':f'Bearer {WHATSAPP_TOKEN}','Content-Type':'application/json'}
    payload={'messaging_product':'whatsapp','to':to,'type':'text','text':{'body':text}}
    async with httpx.AsyncClient(timeout=15) as client:
        r=await client.post(url,headers=headers,json=payload)
        try: return r.json()
        except: return {'status':r.status_code,'text':r.text}
