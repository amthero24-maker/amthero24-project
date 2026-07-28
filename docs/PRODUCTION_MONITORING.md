# AmtHero24 Production Monitoring

This runbook covers the scheduled read-only monitor, automatic GitHub incident lifecycle, and strict release preflight. It contains no production credentials or user data.

## Repository configuration

Set these GitHub repository values before enabling controlled Beta:

### Variables

```text
PRODUCTION_BASE_URL=https://your-production-domain.example
EXPECTED_APP_VERSION=3.4.0
```

`EXPECTED_APP_VERSION` must be updated only after the matching version is deployed. A version mismatch is treated as a failed production check because it can indicate a delayed, partial, or unexpected deployment.

### Secret

```text
ADMIN_API_TOKEN=<same strong token configured in production>
```

Never place the token in a repository variable, workflow input, issue, artifact, log, screenshot, or committed file.

## Scheduled monitor

The `Production Smoke` workflow runs every six hours and can also be started manually. It calls only the read-only endpoints used by `production_smoke.py`:

- `/health`
- `/ready`
- `/admin/launch-readiness` when the admin token is available

It does not send WhatsApp messages, create users, update missions, create reminders, or write application data.

The workflow always runs. If `PRODUCTION_BASE_URL` is missing, it emits a
sanitized `monitor_execution` failure, synchronizes the production incident,
and exits non-zero instead of silently skipping monitoring.

Before declaring an incident, the monitor retries up to three times with a bounded delay. This reduces false incidents from a short network interruption or a container that is still becoming ready. Retry count and delay are capped in code.

The uploaded `production-monitor-report` artifact contains only:

- healthy or unhealthy status
- UTC generation time
- configured and executed attempt counts
- whether a retry recovered
- check name, pass/fail status, and sanitized detail

It intentionally excludes the production URL, phone numbers, messages, documents, headers, response bodies, database URL, and credentials.

## Automatic incident lifecycle

When all retries fail, the workflow creates or updates one issue titled:

```text
[Production Incident] AmtHero24 production checks failing
```

The issue uses the `production-incident` label. Repeated failing checks update the same issue instead of opening duplicates. The body shows only safe aggregate check details and a link to the GitHub Actions run.

When a later scheduled check succeeds, the workflow adds a recovery comment and closes the open incident automatically. A transient failure that recovers during the same retry sequence does not open an incident.

The workflow remains failed while production is unhealthy even after the issue is synchronized. Do not mark a failing run successful merely because an incident issue exists.

## Incident response order

1. Open the linked workflow diagnostics.
2. Check `/health`, `/ready`, and the protected launch report.
3. Check the Railway deployment and PostgreSQL service.
4. Pause Beta invitations.
5. Do not disable webhook signatures, privacy retention, abuse protection, encryption checks, or fail-closed database behavior to hide the symptom.
6. For an application regression, use `Create Rollback PR` with a known-good commit.
7. For database corruption, stop writes and restore first into a separate PostgreSQL service.
8. Run the strict release preflight before directing users to the recovered deployment.

## Strict release preflight

Use the manual `Release Preflight` workflow before a controlled production release. Provide the exact application version expected from `/health`.

The gate requires:

- a reachable healthy service
- exact version match
- PostgreSQL storage
- initialized schemas
- fail-closed database behavior
- enforced Meta webhook signatures
- dedicated reminder encryption
- strong protected-admin access
- a fully ready controlled-Beta launch report
- a recent encrypted backup manifest when backup verification is enabled

The preflight is read-only. It never deploys, rolls back, or mutates user data. A successful preflight is evidence that the checked deployment was ready at that moment; it is not a replacement for continuous monitoring.

## Manual local monitor

```bash
export PRODUCTION_BASE_URL="https://your-production-domain.example"
export ADMIN_API_TOKEN="set-locally-without-committing"
export EXPECTED_APP_VERSION="3.4.0"
python scripts/production_monitor.py --attempts 3 --delay-seconds 20
```

The command exits non-zero when production remains unhealthy and writes `production-monitor.json` with the same sanitized schema used by GitHub Actions.
