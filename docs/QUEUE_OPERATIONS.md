# AmtHero24 Durable Queue Operations

The durable inbound queue is monitored through aggregate-only data. Operator reports do
not expose message IDs, sender hashes, phone numbers, ciphertext, message text, media
identifiers, document content, or user profiles.

## Protected reports

When the queue schema exists, `/admin/overview` includes:

- `mode`: disabled, configured, misconfigured, requires-postgresql, or schema-missing
- `total` and counts grouped by queued, processing, completed, and dead
- `ready`: work eligible for a worker now
- `delayed`: retry work waiting for its backoff time
- `stale_processing`: expired worker leases available for recovery
- `retrying`: active work with more than one processing attempt
- `dead_24h`: work that exhausted or could not safely use its recovery envelope
- `oldest_ready_age_seconds`: age of the oldest recoverable item
- `max_attempt_count`: highest aggregate attempt count

The protected `/admin/launch-readiness` report adds one durable-queue check and
recomputes the launch decision.

## Controlled-Beta thresholds

The launch report blocks expansion when:

- the enabled queue is misconfigured, missing its schema, or not using PostgreSQL
- five or more items entered dead-letter state during the last 24 hours
- five or more processing leases are stale
- the oldest recoverable item has waited 30 minutes or longer

It warns when:

- the queue is intentionally disabled
- any recent dead-letter or stale-lease signal exists
- the oldest recoverable item has waited five minutes or longer
- at least 100 items are ready at once

An empty configured queue is healthy. A disabled queue is not presented as equivalent
to crash-safe recovery; retry-safe webhook idempotency remains active, but launch
readiness stays at warning until the durable queue is enabled.

## Incident order

1. Check `/ready` and confirm `durable_inbound_queue` is `configured`.
2. Check aggregate Groq and WhatsApp provider health.
3. Check `ready`, `stale_processing`, `oldest_ready_age_seconds`, and `dead_24h`.
4. Verify Railway replicas are running and can reach PostgreSQL.
5. Do not expose or manually decrypt queue envelopes in logs, dashboards, or issues.
6. Do not manually redrive dead-letter rows: their sender/media ciphertext is already
   erased. Correct the underlying failure and ask the affected user to resend only when
   the user contacts support through an authorized channel.
7. Keep Beta invitations paused until the launch report returns to warning or ready.

## Retention and privacy

The normal privacy worker also removes expired queue envelopes and old completed/dead
rows. Completion and dead-letter transitions clear recovery ciphertext immediately;
retention then removes the metadata row. User deletion removes the parent inbound
message and PostgreSQL cascades to the queue row.

Queue metrics are operational aggregates, not user analytics. They must not be extended
with per-message identifiers, failure text, phone-derived grouping, or decrypted contact
data.
