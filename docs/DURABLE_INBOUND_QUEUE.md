# AmtHero24 Durable Inbound Queue

Version 4.0 adds an optional PostgreSQL work queue that closes the gap between accepting
a Meta webhook and completing the background task. It is disabled by default so an
existing Railway deployment is not broken before a dedicated encryption key is added.

## What it protects

When enabled, AmtHero24 does not acknowledge a supported user message until both of
these writes have succeeded:

1. the retry-safe message claim
2. an encrypted, short-lived recovery envelope in PostgreSQL

The immediate background task and every application replica compete for the same queue
row with `FOR UPDATE SKIP LOCKED`. If a process restarts, another worker reclaims the row
after its lease expires. If the inbound message is already marked `sent`, recovery
clears the envelope without sending a second reply.

## Privacy design

The queue does **not** duplicate message text. Text and message type remain in the
existing `inbound_messages` table under its normal retention policy.

The recovery envelope contains:

- encrypted sender number
- encrypted Meta media ID only when media exists
- MIME type
- status, lease, attempt count, generic failure code, and timestamps

The sender and media ciphertext use `MESSAGE_QUEUE_ENCRYPTION_KEY`, which must be unique
and at least 32 strong characters. Completion and dead-letter transitions immediately
clear both ciphertext fields. Deleting the user's inbound message cascades to the queue
row, and expired/completed rows are cleaned automatically.

## Safe rollout

Generate a new key in a trusted local environment:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Add it to Railway without placing it in GitHub, screenshots, or chat logs:

```text
MESSAGE_QUEUE_ENCRYPTION_KEY=<new unique value>
```

Keep the feature disabled for the first deployment:

```text
DURABLE_QUEUE_ENABLED=false
```

Verify `/ready` reports:

```text
durable_inbound_queue: disabled
```

Then enable it:

```text
DURABLE_QUEUE_ENABLED=true
DURABLE_QUEUE_POLL_SECONDS=5
DURABLE_QUEUE_MAX_ATTEMPTS=5
DURABLE_QUEUE_ENVELOPE_HOURS=48
DURABLE_QUEUE_COMPLETED_RETENTION_HOURS=24
DURABLE_QUEUE_LEASE_MINUTES=15
```

After restart, `/ready` must report `durable_inbound_queue: configured`. If the key is
missing/weak or PostgreSQL is unavailable, readiness fails and POST `/webhook` returns a
retryable 503 instead of silently dropping a message.

## Delivery semantics

The queue provides durable **at-least-once processing** and suppresses duplicates when
the inbound record is already `sent`. Like any external-message integration without a
provider idempotency key, there remains a very small crash interval after WhatsApp has
accepted an outbound send but before the local `sent` state is committed. The system
minimizes this interval and never claims mathematical exactly-once delivery.

## Operational states

- `queued`: persisted and ready after `available_at`
- `processing`: owned by one worker until `lease_until`
- `completed`: handled; ciphertext cleared
- `dead`: retry budget exhausted or envelope unreadable; ciphertext cleared

Unhandled composition errors use bounded exponential backoff. Errors already handled by
Sam's normal safe-failure response are considered completed, preventing repeated apology
messages. Aggregate queue counts contain no user data and can be added to operator
monitoring later.
