import os
from dotenv import load_dotenv
load_dotenv()
GROQ_API_KEY=os.getenv("GROQ_API_KEY","")
WHATSAPP_TOKEN=os.getenv("WHATSAPP_TOKEN","")
PHONE_NUMBER_ID=os.getenv("PHONE_NUMBER_ID","1264010770128749")
WABA_ID=os.getenv("WABA_ID","2178786346022357")
VERIFY_TOKEN=os.getenv("VERIFY_TOKEN","amthero24_verify_2026")
GROQ_MODEL=os.getenv("GROQ_MODEL","llama-3.3-70b-versatile")
