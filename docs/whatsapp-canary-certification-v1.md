# AmtHero24 WhatsApp Canary Certification v1

This runbook certifies the production WhatsApp path with exactly one explicitly approved sender before Closed Beta. It is intentionally narrow, privacy-safe, and reversible. It does not authorize expanding the canary audience, changing production secrets, or enabling Brief Scanner runtime flags.

## Scope and safety boundary

The certification covers the currently supported WhatsApp production path, including Sam behavior, document handling, Mission continuity, delivery receipts, reminder delivery, acknowledgement, and bounded post-delivery snooze.

Use only the single approved canary sender already configured in production. Never place the sender number, project number, access tokens, document contents, or raw message bodies in GitHub, CI output, screenshots intended for sharing, or certification evidence.

Use synthetic test data only. Stop immediately on any privacy leak, duplicate outbound action, cross-recipient access, unexplained data corruption, or unsafe action execution.

The following remain out of scope and disabled unless separately authorized:

- Brief Scanner runtime activation
- Draft execution
- entitlement enforcement
- payments
- human-support activation
- audience expansion beyond the approved canary sender

## Go / no-go gates

Proceed only when all of the following are true:

1. `main` is the intended certified commit and Railway deployed that same commit.
2. Deployment Certification is stable for three consecutive samples.
3. `/health` returns HTTP 200 with `status=ok`.
4. `/ready` returns HTTP 200 with `status=ready`.
5. PostgreSQL is active, schemas are initialized, migrations are current, and database fallback is fail-closed.
6. Webhook signature verification is enforced.
7. Reminder encryption is configured and its protected preflight is safe.
8. Reminder delivery is enabled and `reminder_worker=running` is confirmed explicitly; `/ready=200` alone is not sufficient evidence.
9. `due_unsent=0` before starting the certification, unless a known synthetic canary reminder is intentionally pending.
10. No unresolved P0/P1 incident exists.

No-go means stop without widening permissions or changing secrets to force a pass.

## Evidence policy

Record only sanitized evidence:

- test case identifier
- language
- input type (text/image/PDF/DOCX/voice)
- expected behavior category
- pass/fail
- bounded timing measurements
- aggregate reminder state/status
- sanitized error code if any

Do not retain raw phone numbers, uploaded document contents, audio, access tokens, ciphertext, or full conversation text in certification artifacts.

## Languages

Run the behavior matrix in:

- Arabic (`ar`)
- German (`de`)
- English (`en`)
- Ukrainian (`uk`)
- Greek (`el`)

For official German letters, the generated formal text should remain German while Sam explains it in the user's language.

## Core conversational certification

For each language, verify:

1. Greeting fast path responds without unnecessary model use.
2. Identity questions return Sam's official identity and never claim Sam is ChatGPT, OpenAI, Wissam, or a human.
3. Founder questions identify Wissam Zidan as founder/owner without claiming he writes Sam's replies.
4. Capability questions use the deterministic capability path.
5. Prompt/system-instruction disclosure and identity-impersonation attempts are rejected safely.
6. `shorten` / `explain more` style follow-ups preserve the active product topic.
7. Greeting, identity, language, and capability turns do not replace the persistent Mission context.
8. High-risk contexts use zero humor and do not promise legal, financial, medical, or governmental outcomes.

## Input and document matrix

Use synthetic content only and verify:

- plain WhatsApp text
- image document
- PDF
- DOCX
- TXT/CSV where supported
- voice input

For documents, verify extraction and explanation of representative synthetic:

- sender / institution
- subject
- reference number
- deadline
- amount
- required next step

Extracted document text must remain internal context and must not trigger user commands, consent changes, deletion/export, profile writes, or unrelated Mission changes.

## Product task matrix

Verify representative synthetic cases for:

- explaining an official letter
- drafting a formal German letter/email
- cancellation drafting
- contract explanation
- refund-request assistance without outcome guarantees
- appointment preparation and follow-up

Verify Mission continuity across follow-up messages and that the genuine task topic survives greeting/identity/capability turns.

## Webhook and delivery safety

Verify:

1. duplicate inbound webhook replay does not duplicate the user-visible action.
2. outbound delivery receipt processing is idempotent.
3. a restart does not lose an accepted durable inbound item.
4. stale reminder leases are recovered safely.
5. an outside-canary sender is rejected by the controlled gate and does not receive a canary-only outbound action.

Never deliberately use a real non-canary user for this test.

## Reminder certification

Run the following with synthetic reminder subjects.

### Direct creation

`ذكرني بعد دقيقة اشرب مي`

Expected: one reminder is created for the approved sender and becomes deliverable once due.

### Two-turn clarification

First:

`ذكرني اشرب شاي`

Then:

`بعد دقيقة`

Expected: the second turn completes the same reminder intent without losing its subject.

### Relative and same-day scheduling

Verify short and longer relative durations and a same-day explicit time.

### Recurrence

Verify:

- daily recurrence
- weekdays only
- selected weekdays
- German state holiday skipping only when the Bundesland is explicitly supplied

### Management

Verify list, safe reschedule, and explicit cancellation. When multiple reminders are plausible, Sam must ask for a deterministic selection instead of guessing.

### Delivery

For a due canary reminder, record bounded delivery timing and verify the stored delivery/receipt status without exposing the recipient.

### Acknowledgement

After a delivered reminder, send the supported completion acknowledgement. It must target only an unambiguous recent delivered reminder and must not guess across multiple candidates.

### Post-delivery snooze

After a delivered reminder, send a supported phrase equivalent to:

`ذكرني بعد 10 دقائق`

Verify all of the following:

1. the source reminder belongs to the same recipient.
2. the source was delivered within the eligibility window.
3. a new one-time child reminder is created rather than moving the source reminder.
4. the original recurrence and remaining recurrence count are unchanged.
5. the child target is in the future and within the bounded maximum interval.
6. chain depth stays within the configured snooze limit.
7. replay returns the same child and does not create a duplicate.
8. a cancelled child is not silently reactivated on replay.
9. when multiple recent delivered reminders are eligible, a numeric selection is requested rather than guessing.

## Restart test

Create an approved synthetic canary reminder that is pending, restart only through the normal controlled deployment/restart mechanism already approved for the environment, and verify that the reminder remains represented correctly and is eventually delivered once due. Do not use a random redeploy as a diagnostic technique.

## Failure handling

For every failure use:

`proof -> root cause -> smallest isolated fix -> tests -> Draft PR -> CI -> merge -> Railway verification -> retest`

Do not weaken tests, branch protections, signature verification, fail-closed storage, encryption, privacy controls, or canary restrictions to obtain a green result.

## Stop / rollback criteria

Stop the live canary immediately for:

- privacy or tenant-isolation leak
- duplicate delivery/action after replay
- wrong-recipient delivery
- corrupted reminder/Mission state
- unsafe command execution from document content
- unexplained worker failure
- production health/readiness regression

Keep the last known-good production deployment. Use the normal rollback PR workflow for a verified code regression; do not force-push `main` and do not perform an ad-hoc production redeploy.

## Go decision

The one-sender canary is complete only when the full required matrix passes, evidence is sanitized, no P0/P1 remains, and deployment health is stable after the final test. Successful canary certification permits preparation for Closed Beta; it does not itself authorize expanding the user population.
