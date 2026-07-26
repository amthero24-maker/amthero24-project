# AmtHero24

AmtHero24 is a FastAPI service that receives WhatsApp Cloud API webhooks and lets **Sam von AmtHero24** help people with daily life and bureaucracy in Germany.

## Architecture

- `app.py` — FastAPI routes, webhook parsing, message orchestration and safe fallbacks.
- `config.py` — non-secret defaults and runtime validation of required environment variables.
- `data_store.py` — temporary atomic JSON persistence and message deduplication.
- `groq_client.py` — Groq text and image request boundary.
- `prompts.py` — Sam's product and safety instructions.
- `whatsapp.py` — WhatsApp Cloud API sending and media download client.
- `tests/` — unit and integration-boundary tests.

JSON persistence is only an MVP bridge. A later sprint will migrate users, missions and messages to PostgreSQL without changing webhook behavior.

## Required environment variables

Set these values in Railway and in a local `.env` file:

- `GROQ_API_KEY`
- `WHATSAPP_TOKEN`
- `PHONE_NUMBER_ID`
- `VERIFY_TOKEN`

Optional settings are documented in `.env.example`. Never commit `.env` or real credentials.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Fill the required values in .env
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

On Windows PowerShell, activate with:

```powershell
.venv\Scripts\Activate.ps1
```

## Validation

```bash
pytest -q
python -m compileall app.py config.py data_store.py groq_client.py prompts.py whatsapp.py main.py
```

## Endpoints

- `GET /` — service information.
- `GET /health` — liveness response without credentials or personal data.
- `GET /webhook` — Meta webhook verification.
- `POST /webhook` — fast acknowledgement and background message processing.

## Privacy

The JSON store hashes phone numbers and does not retain document bytes. Do not store passwords, bank credentials, insurance numbers, tokens or passport images. Users can request deletion using phrases such as `Daten löschen`, `delete my data`, or `امسح بياناتي`.

## Deployment

Railway starts the application with:

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```
