# Brief Scanner Canary activation runbook v1

This runbook enables the read-only Brief Scanner for explicitly approved WhatsApp senders only. It does not enable missions, reminders, drafts, persistence, telemetry, or document-memory writes.

## Preconditions

- `main` contains PRs #78 through #81.
- Production health checks are green before changing Railway variables.
- Use one controlled WhatsApp test number first.
- Store sender values as full international numbers. Formatting characters are accepted, but matching is performed on the complete normalized digit string.

## Railway variables

Keep the feature disabled until the allowlist is configured.

```env
BRIEF_SCANNER_PROVIDER_ENABLED=false
BRIEF_SCANNER_CANARY_SENDERS=491701234567
```

For multiple controlled testers, use a comma-separated list:

```env
BRIEF_SCANNER_CANARY_SENDERS=491701234567,491609876543
```

Never use partial numbers, last four digits, wildcards, or public customer lists.

## Activation order

1. Set `BRIEF_SCANNER_CANARY_SENDERS` to the single controlled test sender.
2. Confirm the deployment is healthy and ordinary text and image replies still work.
3. Set `BRIEF_SCANNER_PROVIDER_ENABLED=true`.
4. Wait for the Railway deployment to become healthy.
5. From the allowlisted number, send one clear JPEG or PNG image of a synthetic, non-sensitive German document.
6. Confirm the reply is concise, in the selected language, and explicitly read-only.
7. From a non-allowlisted number, send the same image and confirm the existing generic vision path remains active.
8. Test one unclear image and confirm the user receives a safe retry message without any automatic action.

## Acceptance criteria

- Only the exact allowlisted sender reaches Brief Scanner.
- No full sender number appears in provider prompts or logs.
- No mission, reminder, draft, or document-memory record is created.
- Unsupported media and malformed or disabled outcomes fall back safely.
- Text-only messages are unchanged.
- Non-allowlisted users remain on the existing production image flow.

## Immediate rollback

Set either variable as follows and redeploy:

```env
BRIEF_SCANNER_PROVIDER_ENABLED=false
```

or:

```env
BRIEF_SCANNER_CANARY_SENDERS=
```

Disabling either gate prevents Brief Scanner from running. The existing generic image flow remains available.

## Stop conditions

Rollback immediately if any of the following occurs:

- a non-allowlisted sender receives a Brief Scanner reply;
- a sender identity, document content, or provider exception appears in logs;
- the reply invents a deadline, amount, authority, obligation, or legal conclusion;
- image processing failure affects ordinary text traffic;
- any mission, reminder, draft, or memory side effect is observed.

## Expansion

Do not add more testers until the single-sender Canary has passed repeated synthetic tests in all supported reply languages. Expand the allowlist gradually and preserve exact full-number matching.