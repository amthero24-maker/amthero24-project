# AmtHero24 Webhook Idempotency

Meta may deliver the same WhatsApp message more than once. Multiple AmtHero24 replicas
may also receive overlapping deliveries. The production webhook therefore uses a
durable message lifecycle instead of treating the first insert as permanently final.

## Lifecycle

1. A new WhatsApp message ID is atomically inserted as `processing`.
2. The winning worker receives a ten-minute lease and increments `attempt_count`.
3. Any simultaneous delivery of the same message ID is acknowledged without starting a
   second worker.
4. A successful response marks the message `sent`; that state is terminal.
5. A processing exception marks the message `failed`; an exact Meta retry may claim it
   immediately.
6. A worker that disappears without updating state loses its lease. A later Meta retry
   may reclaim the message after the lease expires.
7. The same message ID can never be reassigned to a different sender hash.

## Failure policy

The webhook returns HTTP 503 with a short `Retry-After` header when PostgreSQL cannot
create a durable claim. It does not return a false success that could silently lose the
message. Status-only Meta webhooks and malformed unsupported payloads remain safely
acknowledged because they contain no supported user message to process.

## Stored data

The claim record contains only:

- Meta message ID
- one-way sender hash
- bounded text already required for short operational recovery
- message type and whether media exists
- lifecycle status, lease, attempt count, and timestamps

Raw phone numbers and media IDs are not stored. Existing privacy deletion removes the
claim row through the user's sender hash, and normal retention removes old inbound
records.

## CI contract

The atomic JSON suite verifies retries, terminal success, abandoned leases, sender
binding, and concurrent ownership. The real PostgreSQL suite runs competing repository
instances and requires exactly one winner, verifies schema migration, retries failed and
stale claims, and proves terminal messages cannot run twice.
