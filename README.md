# AmtHero24

AmtHero24 is a FastAPI service behind a WhatsApp assistant called **Sam von AmtHero24**. Sam helps people navigate daily life and bureaucracy in Germany in German, Arabic, English, Ukrainian, and Greek.

## Current capabilities

- consent-aware Hero Memory backed by PostgreSQL
- multilingual text, image, PDF, DOCX, TXT, CSV, and voice input
- structured missions and encrypted reminders with bounded recurrence, weekday/holiday scheduling, safe rescheduling, recent-delivery acknowledgement, and independent bounded snoozes that preserve the original schedule
- German official letter and email drafting
- complete user export/deletion and automatic retention
- webhook signature verification, abuse protection, and provider circuit breaking
- protected aggregate admin and Beta launch-readiness endpoints
- subscription-ready entitlement accounting in observe-only mode
- disabled-by-default encrypted human-support handoff
- anonymous Beta feedback metrics
- read-only production smoke checks and guarded database recovery tooling

## Architecture

- `app.py` — FastAPI routes, WhatsApp parsing, onboarding, and message orchestration
- `application.py` and `*_extensions.py` — production composition layers
- `data_store.py` — PostgreSQL storage with an atomic JSON fallback for local/tests
- `hero_memory.py`, `mission_engine.py`, `reminder_engine.py` — memory and follow-up workflow
- `document_service.py`, `document_intelligence.py` — safe document extraction and structured facts
- `groq_client.py`, `provider_reliability.py` — model boundary, telemetry, and circuit breaker
- `webhook_security.py`, `abuse_guard.py` — inbound authenticity and cost protection
- `runtime_health.py`, `admin_metrics.py`, `launch_readiness.py` — production health and launch gates
- `production_smoke.py` — non-mutating external production checks
- `scripts/` — encrypted PostgreSQL backup and guarded restore commands
- `tests/` — unit and integration-boundary regression suite

## Required environment variables

Set these values in Railway and in a local `.env` file:

- `GROQ_API_KEY`
- `WHATSAPP_TOKEN`
- `PHONE_NUMBER_ID`
- `VERIFY_TOKEN`

Production also requires `DATABASE_URL`. Optional and staged settings are documented in `.env.example`. Never commit `.env` or real credentials.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn webhook_security:app --host 0.0.0.0 --port 8000 --reload
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

## Validation

```bash
python -m pytest -q
python -m compileall -q .
```

## Endpoints

- `GET /` — service information
- `GET /health` — liveness and selected backend
- `GET /ready` — configuration, PostgreSQL, and production component readiness
- `GET /webhook` — Meta webhook verification
- `POST /webhook` — signature-protected WhatsApp webhook receiver
- `GET /admin/overview` — protected aggregate operational metrics
- `GET /admin/launch-readiness` — protected controlled-Beta go/no-go report
- `GET /admin/support/tickets` — separately protected minimal support queue when enabled

## Privacy and safety

Phone identifiers are hashed for normal persistence. Reminder and support contact identifiers are encrypted where later delivery or contact requires reversibility. Document and audio bytes are transient. Protected operational endpoints expose aggregates rather than conversations or identity data.

Users can ask what is remembered, export their data, stop reminders, or delete all linked data. Sam does not claim to be a lawyer, doctor, government employee, or replacement for a qualified professional.

## Deployment

Railway starts the application using:

```text
uvicorn webhook_security:app --host 0.0.0.0 --port $PORT
```

Production checks, backups, restore drills, incidents, and rollback procedures are documented in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
