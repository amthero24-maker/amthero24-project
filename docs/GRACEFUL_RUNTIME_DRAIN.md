# AmtHero24 Graceful Runtime Drain

AmtHero24 enters a process-local `draining` phase when Railway begins shutting down a
container. The phase transition happens before worker-specific shutdown callbacks.

## Immediate behavior

As soon as draining starts:

- `/ready` returns HTTP 503 and reports `runtime_lifecycle=draining`
- POST `/webhook` returns HTTP 503 with `Retry-After: 10`
- the durable queue worker stops claiming new items
- background work that has not started does not begin
- already-running work receives only the remaining shared shutdown budget

`/health` remains a liveness endpoint. Railway routes new traffic based on `/ready`, not
`/health`.

## One shared shutdown budget

`SHUTDOWN_GRACE_SECONDS` defaults to 10 seconds and is bounded between 1 and 12 seconds.
The maximum remains below Railway's 15-second drain window, leaving time for the process
itself to finish exiting. The deadline is created once when draining starts. Durable
queue, reminder, privacy, and other registered shutdown handlers consume the same
deadline; they do not each receive a fresh timeout.

Worker shutdown priority is:

1. durable inbound queue
2. reminder worker
3. privacy-retention worker
4. remaining registered callbacks

This priority protects user replies first while still signalling every worker to stop.
A handler that exceeds the remaining budget is cancelled and logged with only its
component name.

## Durable queue interruption

If durable processing is cancelled after it claimed a queue item:

- an inbound message already marked `sent` or `failed` is completed and its recovery
  ciphertext is erased
- unfinished work returns to `queued`
- the processing lease is cleared
- encrypted sender/media recovery data remains encrypted
- retry is delayed by `SHUTDOWN_RETRY_DELAY_SECONDS`, default 30 seconds, bounded between
  5 and 300 seconds

The delay reduces immediate duplicate-delivery risk when an external provider request was
interrupted at an ambiguous point.

When the durable queue is disabled, an unstarted or cancelled claimed message is moved to
the existing retryable `failed` state rather than being left permanently processing.

## Delivery guarantee boundary

AmtHero24 provides retry-safe, at-least-once recovery around external WhatsApp and Groq
calls. No application can prove exactly-once delivery across a network interruption when
the provider accepted a request but the process stopped before receiving or persisting
the response. The drain budget, message lifecycle, outbound delivery receipts, delayed
retry, and terminal-state checks reduce this ambiguity but do not eliminate the external
system boundary.

## Privacy boundary

The lifecycle coordinator stores only:

- phase: `accepting` or `draining`
- aggregate count of active work
- an internal monotonic shutdown deadline

It never receives message IDs, phone numbers, sender hashes, message text, documents,
media IDs, ciphertext, provider responses, or credentials. Public readiness exposes only
the phase, not the active count.

## Operator checks

After deployment:

1. Railway must activate the instance through `/ready`.
2. Deployment Certification must observe `runtime_lifecycle=accepting` in every
   consecutive sample.
3. During a replacement deployment, the old instance should become not-ready before it
   exits.
4. Queue observability should not show growing stale leases or dead letters after rollout.
5. Outbound delivery receipts should be reviewed before retrying any ambiguous external
   send manually.
