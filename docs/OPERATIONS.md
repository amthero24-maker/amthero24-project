# AmtHero24 Production Operations

This runbook covers read-only production checks, fail-closed durable storage, dedicated encryption keys, encrypted PostgreSQL backups, restore drills, and safe application rollback. It deliberately avoids real credentials and user data.

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

For the one-sender WhatsApp Canary, additionally require proof that the Reminder Worker itself is running:

```bash
python production_smoke.py --require-signature --require-launch-ready --require-reminder-worker
```

The equivalent environment switch is `SMOKE_REQUIRE_REMINDER_WORKER=true`. It is false by default so ordinary read-only production monitoring remains backward-compatible. When enabled, `reminder_worker` must be exactly `running`; `/ready=200` by itself is not sufficient evidence that timed deliveries are actively being processed.

The smoke suite requires PostgreSQL, initialized application schemas, fail-closed database behavior, enabled reminder delivery, dedicated reminder encryption, and strong protected-admin access for a production pass.

### GitHub scheduled checks

Set the repository variable `PRODUCTION_BASE_URL`. Add the repository secret `ADMIN_API_TOKEN` to include the protected launch report. The `Production Smoke` workflow then runs every six hours and can also be started manually.

A missing `PRODUCTION_BASE_URL` fails the scheduled job with a sanitized
`monitor_execution` result and opens or updates the production incident. This
keeps missing monitoring configuration visible without exposing the URL.

### Durable storage failure policy

Production must use:

```text
DATABASE_FALLBACK_ALLOWED=false
```

When `DATABASE_URL` is configured and PostgreSQL cannot initialize, the production entrypoint stops instead of accepting traffic into an ephemeral JSON store. This prevents two different copies of user memory from being created across restarts or replicas.

`DATABASE_FALLBACK_ALLOWED=true` exists only as an explicit supervised emergency option. It causes `/ready` to remain unhealthy and blocks the controlled-Beta launch report. Do not use it to hide a database incident.

## 2. Dedicated encryption and operator secrets

Reminder recipients and human-support contacts are reversible because the service must contact them later. They must therefore use dedicated secrets that are independent from WhatsApp, Groq, and database credentials.

Generate unique values of at least 32 characters in a trusted local environment. One safe option is:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Generate a different value for each setting:

```text
REMINDER_ENCRYPTION_KEY
ADMIN_API_TOKEN
SUPPORT_ENCRYPTION_KEY
SUPPORT_API_TOKEN
```

Do not reuse `WHATSAPP_TOKEN` as an encryption key. New reminder records are refused when `REMINDER_ENCRYPTION_KEY` is missing or weak, and the reminder worker does not start. The rest of Sam continues operating and the user receives a localized temporary-unavailability message.

Human support remains disabled unless both its dedicated encryption key and its separate operator API token pass the strength policy.

### Historical reminder compatibility

Earlier versions could derive reminder encryption from `WHATSAPP_TOKEN`. Version 3.2 keeps a temporary read-only compatibility path:

```text
REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED=true
```

This flag never permits new encryption with the WhatsApp token. It only allows historical ciphertext to be read while it is migrated. Never rotate or remove an old WhatsApp token while historical reminders may still depend on it.

### Atomic reminder re-encryption

The migration utility never prints reminder IDs, phone numbers, ciphertext, or secret values. It is dry-run by default.

1. Create and configure the new strong `REMINDER_ENCRYPTION_KEY`.
2. Keep the historical key in one of these temporary variables:
   - `REMINDER_OLD_ENCRYPTION_KEY` for an earlier dedicated key.
   - `REMINDER_LEGACY_WHATSAPP_TOKEN` for ciphertext created from a historical WhatsApp token.
3. Create a fresh encrypted PostgreSQL backup.
4. Run the dry-run while the bot is still online:

```bash
python scripts/migrate_reminder_encryption.py
```

A safe report has `unreadable: 0` and `safe_to_apply: true`. Counts are aggregate only.

5. Stop the bot service so the reminder worker cannot race the migration.
6. Temporarily set:

```text
REMINDER_MIGRATION_ALLOWED=true
```

7. Apply the migration:

```bash
python scripts/migrate_reminder_encryption.py \
  --apply \
  --confirm REENCRYPT_REMINDERS \
  --confirm-bot-stopped BOT_STOPPED
```

Apply mode locks the reminder table, decrypts all rows in memory, aborts the whole transaction if any row is unreadable, updates ciphertext only when the original row is unchanged, and verifies every row with the new key before commit.

8. Run the dry-run again. Every row should be counted under `already_current`.
9. Remove the temporary old-key variables, set `REMINDER_MIGRATION_ALLOWED=false`, and set:

```text
REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED=false
```

10. Start the bot and run the production smoke suite. Do not delete historical key material until the post-migration backup and restore drill have both succeeded.

## 3. Encrypted PostgreSQL backups

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

## 4. Restore drill

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

Every pull request also runs an isolated automated recovery drill. CI creates representative application data, encrypts a real `pg_dump`, restores it into a separate database, and verifies table, row-count, mission, reminder, support, and privacy parity. This proves the recovery code continuously but does not replace testing an actual Railway backup from its persistent volume.

## 5. Application rollback

Use the GitHub Actions workflow `Create Rollback PR`.

Inputs:

- `target_sha`: a known-good commit already contained in `main`
- `confirmation`: `CREATE_ROLLBACK_PR`

The workflow does not force-push and does not deploy directly. It creates a normal pull request whose repository contents match the selected known-good commit. CI must pass before merge.

This rollback affects application code only. It does not reverse database data or schema changes. Database recovery must use a verified backup or a separately reviewed forward migration.

## 6. Incident order

For a production incident:

1. Check Railway deployment and `/health`.
2. Check `/ready` and identify configuration, storage, schema, encryption, or provider failure.
3. Check `/admin/launch-readiness` using the protected token.
4. Pause Beta invitations; do not disable privacy, signature verification, fail-closed database policy, or encryption checks to hide the symptom.
5. For a code regression, create a rollback PR to the last known-good commit.
6. For database corruption, stop writes, preserve evidence, and restore first into a separate service.
7. Run the smoke suite before directing traffic to the recovered version.

## 7. Required production settings

Before controlled Beta, the launch report should confirm:

- PostgreSQL active
- all PostgreSQL application schemas initialized
- `DATABASE_FALLBACK_ALLOWED=false`
- `META_APP_SECRET` configured
- `WEBHOOK_SIGNATURE_REQUIRED=true`
- strong unique `ADMIN_API_TOKEN`
- strong unique `REMINDER_ENCRYPTION_KEY`
- `REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED=false` after migration
- `REMINDER_MIGRATION_ALLOWED=false` outside a supervised migration
- privacy retention enabled
- provider telemetry enabled
- abuse protection enabled
- approved Meta utility template configured for reminders outside the 24-hour window
- strong unique support encryption/API tokens before enabling human support

Never place real values in GitHub files, issues, screenshots, or chat messages.

## 8. One-sender WhatsApp Canary

The production certification procedure for the controlled one-sender WhatsApp canary is maintained in [`whatsapp-canary-certification-v1.md`](whatsapp-canary-certification-v1.md).

Before any live canary action, run the strict smoke gate with `--require-reminder-worker` and confirm a stable Deployment Certification for the exact deployed `main` SHA. Use only synthetic content and the already approved sender. Do not widen `REMINDER_CANARY_SENDERS`, change Railway variables, or treat a healthy `/ready` response as delivery-worker proof.

Any failure found by the canary follows the normal isolated-fix path: evidence, root cause, smallest fix, tests, Draft PR, CI, merge, Railway verification, and retest. Stop immediately on privacy leakage, wrong-recipient behavior, duplicate delivery, state corruption, or an unexplained worker failure.
