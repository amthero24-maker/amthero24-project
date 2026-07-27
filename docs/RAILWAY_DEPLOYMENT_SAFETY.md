# AmtHero24 Railway Deployment Safety

AmtHero24 uses two separate deployment protections:

1. Railway calls `/ready` before routing production traffic to a new deployment.
2. A post-deployment certification requires multiple consecutive healthy production
   samples before the Beta cohort is expanded.

The first protection prevents a clearly unready container from becoming active. The
second catches brief readiness, version, provider, queue, or launch-gate instability
after traffic has already switched.

## Railway configuration

`railway.json` is the repository source of truth for:

- the explicit production start command
- healthcheck path `/ready`
- 300-second startup healthcheck timeout
- restart on failure with at most 10 retries
- 30-second overlap between the old and new deployment
- 15-second graceful drain window

`/health` remains a lightweight liveness endpoint. It must not be used as the Railway
deployment gate because it can return HTTP 200 before PostgreSQL, schemas, webhook
idempotency, the durable queue, and encryption-dependent components are ready.

The production entrypoint is declared explicitly in `railway.json` and mirrored in
`Procfile`:

```text
web: uvicorn webhook_security:app --host 0.0.0.0 --port $PORT
```

This is the only accepted entrypoint because `webhook_security` installs log redaction,
storage fail-closed policy, encryption policy, Meta signature verification, all product
composition layers, schema bootstrap, and readiness routes before serving traffic.
The repository contract requires the explicit `startCommand`; a `Procfile` fallback
alone is not accepted.

## Repository contract

`railway_contract.py` validates the deployment configuration offline. It rejects:

- a liveness-only healthcheck path
- a timeout too short for bounded schema and connection startup
- disabled or unbounded restart behavior
- an overlap/drain combination that can terminate in-flight requests immediately
- a missing or unsafe explicit production entrypoint
- removal of Railway's official JSON schema reference

The validator never contacts Railway and never reads runtime environment values.

## Post-deployment certification

The `Deployment Certification` GitHub Actions workflow is manually triggered after
Railway reports the deployment active. It requires repository secrets:

- `PRODUCTION_BASE_URL`
- `ADMIN_API_TOKEN`

The operator supplies the exact expected AmtHero24 version. By default, certification
requires three consecutive healthy samples separated by 30 seconds. Every sample checks:

- `/health` and exact application version
- `/ready`
- PostgreSQL and initialized schemas
- database fail-closed behavior
- enforced Meta signatures
- retry-safe webhook idempotency
- durable inbound queue is either safely disabled or correctly configured
- outbound delivery receipt tracking
- reminder worker and encryption
- privacy retention, provider telemetry, and abuse protection
- protected launch-readiness decision

A single failed sample rejects certification immediately. A later recovery does not turn
the same certification run green; the deployment must be investigated and a new full
certification run started.

## Evidence and privacy

The workflow uploads `deployment-stability.json` for 14 days. The report contains only:

- stable or unstable status
- sample counts
- bounded check names, status, and operational detail
- generation timestamp

It never contains the production URL, admin token, authorization headers, response
bodies, phone numbers, message identifiers, user content, document content, or database
credentials.

## Operator sequence

1. Merge only after all required GitHub checks pass.
2. Wait for Railway to mark the deployment active through `/ready`.
3. Run `Deployment Certification` with the exact new version.
4. Keep the Beta cohort unchanged unless certification is stable.
5. On failure, inspect protected aggregate launch and queue/delivery metrics. Do not send
   synthetic WhatsApp messages or decrypt user envelopes as part of diagnosis.
