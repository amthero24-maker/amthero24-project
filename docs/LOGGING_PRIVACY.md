# AmtHero24 Logging Privacy

AmtHero24 logs operational events, not conversations. Phone numbers, message bodies, documents, authorization headers, database credentials, encryption material, and provider tokens must never be intentionally logged.

## Runtime safety boundary

`webhook_security.py` installs `log_safety.install_logging_safety()` before storage, encryption, application, provider, or webhook composition occurs.

The global `LogRecord` factory protects handlers created later by Uvicorn and libraries. Before formatting, it sanitizes:

- message templates and interpolation arguments
- mappings, lists, tuples, sets, and extra record fields
- bytes and document-like buffers
- Bearer and Basic authorization values
- known provider and platform token formats
- passwords embedded in database URLs
- secret query parameters
- international phone-number patterns
- sensitive JSON-like fields such as `message`, `body`, `text`, `payload`, `recipient`, and `authorization`
- platform message identifiers passed through structured extras
- exception messages and stack-info text
- exact sensitive values currently loaded from environment variables

Long text is truncated after redaction. Binary values are replaced with their byte length only. Raw traceback frames are omitted from sanitized exception logs because source lines can contain fixture or request values.

This filter is a last safety boundary, not permission to log user data. Engineers must continue using stable event names and aggregate fields.

## Allowed logging

Good examples:

```python
logger.info("reminder worker started")
logger.warning("provider call failed", extra={"provider": "groq", "error_code": "timeout"})
logger.error("WhatsApp send failed")
```

Useful safe fields include provider name, operation name, stable error category, HTTP status code, latency, retry count, aggregate counts, application version, and storage backend name.

## Prohibited logging

Do not pass these values directly to logging calls:

- phone, recipient, or WhatsApp ID
- inbound or outbound message text
- webhook body or payload
- document text, bytes, or OCR output
- request or response headers
- access tokens, API keys, app secrets, passwords, or cookies
- database URLs
- ciphertext

Do not use `print()` in runtime service modules. Operator CLI scripts are excluded because their explicit terminal output is reviewed separately.

When a sensitive value is absolutely required in a local diagnostic, wrap it with `redact_text()` or `redact_value()` before the logging call. This should be exceptional; a stable category is usually more useful.

## Static policy

Run locally:

```bash
python scripts/validate_logging_policy.py
```

The policy scans runtime Python modules, excluding `tests/`, `scripts/`, and the read-only production smoke CLI, and rejects runtime `print()` calls, direct sensitive identifiers, sensitive f-strings/concatenation, and sensitive `extra` mappings.

`message_id` is the only structured identifier permitted directly in `extra`, because the global runtime factory always replaces its value before formatting. Do not extend this allowlist without equivalent runtime redaction and regression tests.

The report contains only file, line, rule, and identifier. It does not read runtime values.

## Automated workflow

The `Log Safety` workflow runs on relevant pull requests, pushes to `main`, weekly schedules, and manually. It validates policy, tests runtime redaction, uploads sanitized evidence, and fails if either policy or tests fail.

## Incident response

If logs are suspected to contain personal data or credentials:

1. Stop exporting or sharing the affected logs.
2. Rotate any potentially exposed credential immediately.
3. Restrict access to the affected Railway/GitHub log or artifact.
4. Determine the time range and data categories without copying sensitive values into an issue.
5. Fix the logging call and add a regression test.
6. Redeploy and verify the new logs with synthetic data.
7. Follow the privacy incident process and legal review appropriate to the affected data.
8. Delete retained copies where policy and platform controls allow.

Runtime redaction reduces exposure but cannot make an intentionally logged conversation acceptable.
