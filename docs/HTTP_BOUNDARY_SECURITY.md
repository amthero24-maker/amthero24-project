# AmtHero24 HTTP Boundary Security

AmtHero24 places one hardened ASGI boundary outside Meta signature verification and the FastAPI application. This ensures successful responses and early rejection responses receive the same privacy and browser-security controls.

## Request correlation

Every HTTP request receives a fresh random 128-bit correlation identifier. The service never accepts a client-supplied request ID as authoritative.

The identifier is:

- stored temporarily in the ASGI request state
- returned as `X-Request-ID`
- attached to sanitized log records created during the request
- cleared when request processing finishes

It is random and contains no phone number, account ID, IP address, document value, or message identifier. It exists only to correlate a response with safe operational logs.

## Response headers

The boundary applies these headers to normal and error responses:

```text
Cache-Control: no-store, max-age=0
Pragma: no-cache
Expires: 0
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'
Permissions-Policy: accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-Robots-Tag: noindex, nofollow, noarchive
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
X-Request-ID: <random identifier>
```

`Strict-Transport-Security` is added only when the request is HTTPS or Railway's trusted forwarding metadata reports HTTPS. Plain local HTTP tests do not receive HSTS.

The boundary removes downstream `Server` and `X-Powered-By` headers and overrides weaker cache or browser-security values.

No permissive CORS policy is added. AmtHero24 is a WhatsApp webhook and protected operator API, not a public browser API.

## Request body limits

Defaults:

```text
HTTP_DEFAULT_MAX_BODY_BYTES=262144
HTTP_WEBHOOK_MAX_BODY_BYTES=2097152
```

Non-webhook HTTP requests are limited to 256 KiB. `POST /webhook` has a separate 2 MiB boundary for Meta payloads. Both declared `Content-Length` and streamed bytes are enforced.

Invalid, negative, or conflicting `Content-Length` headers return `400`. Oversized requests return `413` before application processing whenever possible.

The webhook signature middleware uses the same webhook limit, avoiding contradictory nested boundaries.

## Timeout

```text
HTTP_REQUEST_TIMEOUT_SECONDS=30
```

A request that does not complete within the bounded timeout is cancelled and receives a generic `504` response with `Retry-After: 5`, provided a response has not already started.

Meta may retry a timed-out or failed webhook delivery. AmtHero24's retry-safe webhook lifecycle and idempotency store prevent a successfully claimed message from being processed concurrently or duplicated after completion.

## HSTS

```text
HTTP_HSTS_MAX_AGE_SECONDS=31536000
```

Set the value to `0` only for a supervised environment that intentionally disables HSTS. Production should use HTTPS and retain a long max-age. The value is bounded to prevent accidental invalid configuration.

## Generic errors

Boundary-generated errors disclose only stable categories:

```json
{"status":"invalid_request"}
{"status":"request_too_large"}
{"status":"request_timeout"}
{"status":"internal_error"}
```

They never return exception messages, credentials, request bodies, phone numbers, database details, or stack traces.

Unexpected exceptions are logged through the global privacy-safe logging factory. The random request ID is preserved for correlation, while personal and credential data remains redacted.

## Deployment validation

After deployment:

1. Request `/health` and verify `X-Request-ID` and no-store headers.
2. Request `/ready` and verify the same security headers.
3. Confirm HTTPS responses include HSTS.
4. Confirm `Server` and `X-Powered-By` are absent.
5. Send a valid signed Meta webhook and confirm normal processing.
6. Send an invalid signature and confirm `403` still includes security headers.
7. Run Production Smoke and Release Preflight.

Do not increase body or timeout limits merely to conceal a slow provider, oversized client payload, or application defect. Investigate the source and retain the smallest practical boundary.
