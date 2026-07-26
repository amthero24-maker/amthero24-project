# AmtHero24 Graceful Worker Drain

AmtHero24 uses Railway overlap and draining windows to hand traffic from an old
container to a new one. Version 4.4 introduced process-local lifecycle coordination and
process-owned queue/reminder leases. Version 4.4.1 makes every worker consume one shared
shutdown deadline instead of receiving independent timeouts.

## Lifecycle

A process moves through four aggregate states:

- `starting`: application composition and schema bootstrap are not complete
- `accepting`: the process may receive webhooks and claim background work
- `draining`: readiness is false and new webhook work is rejected with retryable 503
- `stopped`: all coordinated shutdown handlers have completed or exhausted the shared
  budget

`/health` remains a liveness endpoint. `/ready` returns HTTP 503 while starting or
draining and exposes only the lifecycle state and aggregate active-work count. It never
contains message IDs, phone numbers, sender hashes, reminder IDs, text, or ciphertext.
Production smoke and deployment certification require `process_lifecycle=accepting`.

## Webhook admission

During drain, POST `/webhook` is rejected before reading or validating the request body:

```text
HTTP 503
Retry-After: 10
Cache-Control: no-store
{"status":"draining"}
```

Meta can retry against the replacement deployment. GET webhook verification and health
endpoints remain available.

## Process-owned leases

The PostgreSQL queue and reminder tables contain a random process-local `lease_owner`
only while an item is in `processing`. It is not derived from a user, message, host,
Railway project, or secret.

At shutdown:

1. the process changes to `draining` before worker shutdown handlers run;
2. no new queue claim is admitted;
3. admitted queue work receives the remaining shared grace period before the polling
   task is cancelled;
4. unfinished queue leases owned by this process return to `queued`;
5. reminder and privacy workers receive only the time left after queue drain;
6. unfinished reminder leases owned by this process return to retryable `failed` state;
7. leases owned by another replica are never modified;
8. the process moves to `stopped` after coordinated shutdown.

Normal completion, retry, blocking, cancellation, and dead-letter transitions clear the
lease owner.

## One process-wide timeout

`GRACEFUL_DRAIN_TIMEOUT_SECONDS` defaults to 12 seconds and is bounded to 1–12 seconds.
The deadline starts once at the first transition to `draining`. Every later worker sees
only the remaining time; it cannot start a fresh five- or twelve-second timeout.

Railway's configured drain window is 15 seconds. Capping application work at 12 seconds
leaves time for task cancellation, connection-pool closure, log flushing, and interpreter
exit before the platform terminates the old container.

Worker shutdown priority is:

1. durable inbound queue
2. reminder worker
3. privacy-retention worker
4. remaining registered callbacks
5. final `stopped` transition

A worker is cancelled immediately when no shared time remains. Queue and reminder lease
release still remains scoped to the current process owner.

## Delivery semantics

Graceful drain reduces avoidable lease delays and duplicate processing. It does not
claim mathematical exactly-once delivery across Meta's external API. If an outbound API
call succeeded but the process stopped before recording the result, AmtHero24 does not
blindly send a duplicate. Delivery receipts and existing inbound sent-state suppression
remain the source of operational evidence.

## Incident checks

When a deployment remains in `starting` or `draining` unexpectedly:

1. inspect `/ready` and the Railway deployment state;
2. verify PostgreSQL and schema bootstrap;
3. inspect aggregate durable-queue and reminder health;
4. keep Beta expansion paused;
5. do not disable webhook signatures, encryption, fail-closed storage, or lease ownership
   to force readiness.
