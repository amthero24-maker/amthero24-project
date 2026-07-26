# AmtHero24 Outbound Delivery Receipts

AmtHero24 records the lifecycle of outbound WhatsApp messages so operators can
distinguish an accepted API request from a message that was delivered, read, or failed.
The feature is operational telemetry only; it does not expose or retain message content
or recipient identity.

## Lifecycle

After Meta accepts an outbound request, the returned message ID is immediately converted
to a one-way SHA-256 hash. The raw ID is not stored. Signed webhook status events update:

- `accepted`: Meta returned an outbound message ID
- `sent`: Meta accepted the message into its delivery network
- `delivered`: the message reached the recipient device
- `read`: Meta reported that the recipient opened it
- `failed`: Meta reported a delivery failure

Duplicate events are idempotent. Success timestamps retain the earliest event. Failure
timestamps retain the latest event. `delivered` and `read` take precedence over an
earlier or later failed event because successful device delivery proves the message was
not ultimately undelivered.

Unknown message IDs are ignored rather than creating unbounded records from webhook
traffic.

## Stored fields

The table contains only:

- SHA-256 hash of Meta's outbound message ID
- bounded message kind such as text, template, image, or document
- current lifecycle status
- accepted, sent, delivered, read, and failed timestamps
- Meta's generic numeric/alphanumeric failure code
- retention and update timestamps

It never stores recipient phone numbers, sender hashes, text, template parameters,
media IDs, error titles/messages/details, webhook payloads, or document content.

## Protected operator metrics

`/admin/overview` exposes aggregate counts for the last 24 hours:

- tracked messages grouped by current status
- terminal messages
- delivered/read success percentage among terminal outcomes
- messages still accepted/sent after 15 minutes
- age of the oldest pending message

`/admin/launch-readiness` blocks Beta growth when pending delivery becomes material or
terminal delivery success drops sharply. Smaller failures and delayed receipts create a
warning so the cohort remains fixed while Meta account, token, template, and service
health are reviewed.

## Failure handling

Status webhook persistence returns HTTP 503 with `Retry-After` when PostgreSQL is
unavailable. Meta can retry the signed status event. The normal inbound message route
remains protected by its separate durable queue and idempotency lifecycle.

If tracking persistence fails after an outbound API request already succeeded, AmtHero24
does not resend the user message. Resending could create duplicates; the failure is
recorded through privacy-safe logs and provider telemetry instead.

## Retention and recovery

`OUTBOUND_DELIVERY_RETENTION_DAYS` defaults to 30 days and is bounded between 1 and 180
days. Expired hashed lifecycle rows are removed by the existing privacy-retention
worker. The table is included in the encrypted PostgreSQL backup and restore drill.

Delivery receipts improve operational evidence but do not provide mathematical
exactly-once delivery across the external WhatsApp API. Meta's status events remain the
source of truth for observed delivery state.
