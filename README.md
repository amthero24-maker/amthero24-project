# AmtHero24 WhatsApp Webhook v1.1

FastAPI webhook for **AmtHero24 — Der Alltagsheld für Deutschland**. Incoming WhatsApp Cloud messages are acknowledged immediately and processed in a FastAPI background task. Groq generates the answer and Meta's Graph API sends it back.

## Features

- Meta webhook verification (`GET /webhook`) and inbound callback (`POST /webhook`)
- immediate `200 OK`; Groq and WhatsApp network calls run after acknowledgement
- fixed Groq model `llama-3.3-70b-versatile`
- WhatsApp Phone Number ID `1264010770128749` for registered number `+49 176 16320301`
- atomic JSON persistence, hashed phone numbers, message deduplication, and 24-hour free-message cleanup
- AmtHero24 tone, dual-language formatting, legal caution, and high-risk escalation prompt
- health endpoint at `GET /health`

## Setup

Requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill these secrets in `.env`:

```dotenv
GROQ_API_KEY=...
WHATSAPP_TOKEN=...
VERIFY_TOKEN=choose-a-random-verification-secret
```

`PHONE_NUMBER_ID` defaults to `1264010770128749`. `GROQ_MODEL` is deliberately a code constant and cannot become empty through a missing environment value.

## Run

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
curl http://127.0.0.1:8000/health
```

Configure Meta with the public HTTPS callback URL `https://YOUR_HOST/webhook` and the same `VERIFY_TOKEN`. Subscribe the WhatsApp Business Account to message events.

## Webhook lifecycle

1. FastAPI parses supported text, button, and interactive replies.
2. It adds each message to `BackgroundTasks` and immediately returns `{"status":"accepted"}`.
3. The worker atomically claims the WhatsApp message ID, preventing duplicate replies.
4. Groq generates an AmtHero24 answer.
5. The answer is split at WhatsApp's 4,096-character text limit and sent via Graph API.
6. Minimal delivery state is saved under `DATA_STORE_PATH` (default `data/store.json`).

Status callbacks and unsupported message types are safely acknowledged and ignored. Document/image OCR is intentionally not claimed by this text webhook because no OCR provider is configured.

## Data and production notes

The JSON store never saves the inbound text or raw phone number. It stores a SHA-256 phone lookup value, message ID, timestamps, and processing status. Free message metadata expires after 24 hours. The store uses an in-process lock and atomic file replacement.

Secure the host and `.env`, restrict access to the data directory, and back it up only in line with user consent. For multiple processes or multiple application hosts, replace the local JSON store with a transactional shared database; an in-process lock cannot coordinate separate workers.

## Tests

```bash
pytest -q
python -m compileall app.py config.py data_store.py whatsapp.py tests
```

The suite covers configuration, webhook verification and fast queueing, Groq model selection, persistent/deduplicated concurrent writes, expiry cleanup, and WhatsApp request construction.

## Product specification

See [AmtHero24 Operating System v1.0](docs/amthero24-os-v1.md) for voice, safety, privacy, and product principles used by the v1.1 webhook implementation.
