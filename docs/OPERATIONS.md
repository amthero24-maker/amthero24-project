# AmtHero24 Production Operations

This runbook covers read-only production checks, encrypted PostgreSQL backups, restore drills, and safe application rollback. It deliberately avoids real credentials and user data.

## 1. Production smoke checks

The smoke checker calls only:

- `GET /health`
- `GET /ready`
- `GET /admin/launch-readiness` when an admin token is provided

It never sends a WhatsApp message or writes application data.

```bash
export PRODUCTION_BASE_URL="https://your-production-domain.example"
export ADMIN_API_TOKEN="set-locally-without-committing"
python production_smoke.py --require-signature
```

For a strict Beta gate:

```bash
python production_smoke.py --require-signature --require-launch-ready
```

### GitHub scheduled checks

Set the repository variable `PRODUCTION_BASE_URL`. Add the repository secret `ADMIN_API_TOKEN` to include the protected launch report. The `Production Smoke` workflow then runs every six hours and can also be started manually.

A missing `PRODUCTION_BASE_URL` causes the scheduled job to skip instead of producing false incidents.

## 2. Encrypted PostgreSQL backups

Run backups inside Railway or another environment that can access the private `DATABASE_URL`.

Requirements:

- PostgreSQL client tools (`pg_dump`, `pg_restore`, `psql`)
- a persistent mounted directory such as `/backups`
- a Fernet encryption key in `BACKUP_ENCRYPTION_KEY`

Generate the key once in a trusted local environment:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store that key in Railway variables and in a separate secure recovery location. Losing it makes encrypted backups unusable.

Create a backup:

```bash
export BACKUP_OUTPUT_DIR=/backups
export BACKUP_KEEP_COUNT=14
python scripts/postgres_backup.py
```

The command creates:

- an encrypted `.dump.fernet` artifact
- a `.manifest.json` file with integrity hashes and non-secret metadata

The database URL is passed through the child process environment and is not printed or included in the `pg_dump` argument list.

### Scheduling

Create a dedicated Railway cron service from this repository with a persistent volume mounted at `/backups`. Its command should be:

```text
python scripts/postgres_backup.py
```

Use a daily schedule during Beta. Keep at least 14 successful backups. A backup stored only on the bot container's ephemeral filesystem is not a valid backup.

## 3. Restore drill

A restore is destructive. Always test against a separate empty PostgreSQL service first.

Two independent safeguards are required:

```bash
export RESTORE_ALLOWED=true
python scripts/postgres_restore.py /backups/amthero24-YYYYMMDDTHHMMSSZ.dump.fernet \
  --confirm RESTORE_AMTHERO24
```

The restore utility:

1. verifies the encrypted artifact hash
2. decrypts into a temporary directory
3. verifies the plaintext dump hash
4. converts the custom dump to SQL
5. runs `psql` with `ON_ERROR_STOP=on`
6. removes temporary plaintext automatically

The database URL is not placed in the restore command arguments.

After a drill, run:

```bash
python production_smoke.py --base-url "https://the-drill-service.example" --require-signature
```

A backup strategy is considered complete only after a successful restore drill has been documented with date, artifact, target, and result.

## 4. Application rollback

Use the GitHub Actions workflow `Create Rollback PR`.

Inputs:

- `target_sha`: a known-good commit already contained in `main`
- `confirmation`: `CREATE_ROLLBACK_PR`

The workflow does not force-push and does not deploy directly. It creates a normal pull request whose repository contents match the selected known-good commit. CI must pass before merge.

This rollback affects application code only. It does not reverse database data or schema changes. Database recovery must use a verified backup or a separately reviewed forward migration.

## 5. Incident order

For a production incident:

1. Check Railway deployment and `/health`.
2. Check `/ready` and identify configuration, storage, or provider failure.
3. Check `/admin/launch-readiness` using the protected token.
4. Pause Beta invitations; do not disable privacy or signature verification to hide the symptom.
5. For a code regression, create a rollback PR to the last known-good commit.
6. For database corruption, stop writes, preserve evidence, and restore first into a separate service.
7. Run the smoke suite before directing traffic to the recovered version.

## 6. Required production settings

Before controlled Beta, the launch report should confirm:

- PostgreSQL active
- `META_APP_SECRET` configured
- `WEBHOOK_SIGNATURE_REQUIRED=true`
- `ADMIN_API_TOKEN` configured
- privacy retention enabled
- provider telemetry enabled
- abuse protection enabled
- reminder encryption configured
- approved Meta utility template configured for reminders outside the 24-hour window

Never place real values in GitHub files, issues, screenshots, or chat messages.
