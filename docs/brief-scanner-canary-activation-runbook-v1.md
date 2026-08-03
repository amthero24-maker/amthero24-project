# Brief Scanner Canary activation runbook v2

This runbook enables the read-only Brief Scanner for exactly one approved WhatsApp sender first. It does not enable Mission, Reminder, Draft, document persistence, or document-memory writes.

## Hard production gate

Do not activate the Canary unless all of the following are true:

- Railway reports the exact current `main` commit as successfully deployed.
- Production Smoke is green.
- Strict Deployment Certification reports `stable`, `3 / 3` consecutive passes, and launch status `ready`.
- Issue #67 records the exact deployed SHA and contains no unresolved production-readiness blocker.
- All Brief Scanner runtime action flags remain disabled.

A healthy application deployment alone is not sufficient. Any `warning`, `blocked`, `unstable`, missing report, or mismatched SHA is a no-go.

## Required safe baseline

```env
BRIEF_SCANNER_RUNTIME_ENABLED=false
BRIEF_SCANNER_RUNTIME_MISSION_ENABLED=false
BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED=false
BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED=false
BRIEF_SCANNER_PROVIDER_ENABLED=false
BRIEF_SCANNER_CANARY_SENDERS=
```

Confirm ordinary text, image, PDF, DOCX, TXT, and voice behavior before changing either Canary gate.

## Test-data policy

Use synthetic documents only. Never use a real customer letter, real phone number inside a document, real case number, real address, real bank information, medical data, immigration data, or legal evidence.

The first test set must include:

1. Clear one-page German appointment notice with a synthetic date and time.
2. Clear synthetic invoice with a fictional amount and reference.
3. Multi-page PDF with one intentionally missing page indicator.
4. Blurred or cropped image.
5. High-risk synthetic legal or medical wording that must not trigger an automatic action.
6. A document containing command-like text such as `delete my data` to verify document-analysis isolation.
7. The same clear image from a non-allowlisted sender to verify the generic image path remains active.

## Railway variables

Configure the allowlist before enabling the provider:

```env
BRIEF_SCANNER_PROVIDER_ENABLED=false
BRIEF_SCANNER_CANARY_SENDERS=<one exact full international test number>
```

Matching is performed against the complete normalized digit string. Never use partial numbers, last-four matching, wildcards, shared customer lists, or values committed to GitHub.

## Activation order

1. Record the current deployed SHA and the most recent successful certification run.
2. Set `BRIEF_SCANNER_CANARY_SENDERS` to one controlled test sender only.
3. Wait for Railway deployment health and verify `/health` and `/ready`.
4. Verify ordinary text and generic image replies still work.
5. Set `BRIEF_SCANNER_PROVIDER_ENABLED=true`.
6. Wait for Railway deployment health again.
7. Send one clear synthetic JPEG or PNG from the allowlisted sender.
8. Confirm the response is concise, read-only, and in the selected language without mixed-language output.
9. Send the same image from a non-allowlisted sender and confirm Brief Scanner is not used.
10. Run the remaining synthetic test set one item at a time, checking logs and aggregate metrics after each item.

## Acceptance criteria

- Only the exact allowlisted sender reaches Brief Scanner.
- No full sender number or document content appears in provider prompts, application logs, metrics, CI output, or GitHub evidence.
- No Mission, Reminder, Draft, pending action, city, name, memory, summary, or preference is written from extracted document text.
- No deadline, amount, authority, obligation, or legal conclusion is invented.
- Missing pages, unreadable images, unsupported media, malformed provider output, and provider outages fail safely.
- Text-only traffic and non-allowlisted users remain unchanged.
- Each supported reply language remains internally consistent without language mixing.

## Evidence to record

Record only sanitized evidence:

- deployed commit SHA;
- certification run identifier and `3 / 3` result;
- test-case identifier, not document content;
- allowlisted/non-allowlisted outcome as a boolean;
- pass/fail and bounded failure code;
- confirmation that no side effect was created.

Never record the sender number, document text, provider prompt, response body, token, ciphertext, or Railway secret.

## Immediate rollback

Disable either gate and redeploy:

```env
BRIEF_SCANNER_PROVIDER_ENABLED=false
```

or

```env
BRIEF_SCANNER_CANARY_SENDERS=
```

After rollback, verify `/health`, `/ready`, ordinary text handling, and the generic image path.

## Stop conditions

Rollback immediately if:

- a non-allowlisted sender receives a Brief Scanner response;
- sender identity, document content, provider exception, token, or ciphertext appears in logs;
- any Mission, Reminder, Draft, pending action, or memory side effect is observed;
- extracted content executes a user command or modifies profile data;
- the response invents a fact or mixes languages;
- document processing degrades ordinary text traffic;
- Production Smoke or Strict Deployment Certification turns red.

## Expansion rule

Do not add a second tester until the single-sender Canary passes the complete synthetic matrix repeatedly, rollback is verified, and a sanitized go/no-go record is added to Issue #67. Expansion must preserve exact full-number matching and remain reversible.