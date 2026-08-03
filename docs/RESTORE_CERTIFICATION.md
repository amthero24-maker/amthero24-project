# AmtHero24 Isolated Railway Restore Certification

This runbook proves that one real encrypted production backup can be restored without modifying the production database or directing real traffic to the restored environment.

## Scope and safety boundary

The restore must use:

- the existing encrypted artifact and matching manifest from the Backup service volume
- a separate disposable Railway PostgreSQL service
- the one-shot `railway.restore.certification.json` profile
- no WhatsApp delivery, real-user traffic, DNS change, or production database mutation

Never paste database URLs, encryption keys, hashes, artifact contents, phone numbers, messages, or documents into GitHub issues, logs, screenshots, or chat.

## Preconditions

Before applying the restore profile, verify all of the following:

1. The Backup service has a persistent volume mounted at `/backups`.
2. `RESTORE_ARTIFACT` identifies one existing `/backups/amthero24-*.dump.fernet` file.
3. The matching `<artifact>.manifest.json` exists.
4. A new isolated PostgreSQL service has been created for this drill.
5. The isolated target is not referenced by the production bot and has no real-user traffic.
6. The Backup service still has its production `DATABASE_URL` only so the restore guard can prove that the target identity is different.

## One-shot variables

Configure these only on the controlled restore execution:

```text
RESTORE_ALLOWED=true
RESTORE_ARTIFACT=/backups/amthero24-YYYYMMDDTHHMMSSZ.dump.fernet
RESTORE_TARGET_DATABASE_URL=<reference to the isolated PostgreSQL service>
RESTORE_TARGET_CONFIRMATION=ISOLATED_RESTORE_TARGET
```

The existing `BACKUP_ENCRYPTION_KEY` remains required. Do not copy its value into a command or issue.

The entrypoint stores the original `DATABASE_URL` as `RESTORE_SOURCE_DATABASE_URL`, replaces `DATABASE_URL` in the child environment with the isolated target, and rejects a target whose password-free libpq identity matches the source. Database URLs are not placed in the restore process argument list.

## Execute

Apply `railway.restore.certification.json` to the service that owns the `/backups` volume and run it once. The profile has:

- `Dockerfile.backup`
- `sh scripts/restore_certification_entrypoint.sh`
- restart policy `NEVER`
- no cron schedule
- no health check

The entrypoint fails closed unless the artifact is encrypted, remains inside the mounted volume, has a matching manifest, the explicit restore flag is enabled, and the isolated-target confirmation is exact.

The restore utility then:

1. validates the manifest artifact name
2. verifies the encrypted artifact checksum
3. validates the current schema version, ledger, checksum, and schema contract
4. decrypts into a temporary directory
5. verifies the plaintext checksum
6. generates SQL with `pg_restore --exit-on-error`
7. applies SQL with `psql` and `ON_ERROR_STOP=on`
8. verifies the restored schema identity against the manifest
9. removes temporary plaintext automatically

The only acceptable terminal result is sanitized JSON containing:

```json
{"status":"verified","schema_contract":"valid"}
```

The output may also include the artifact filename, manifest filename, format, and schema version. It must not include URLs, credentials, hashes, ciphertext, or database contents.

## Restore normal Backup operation

Immediately after the one-shot run:

1. Set `RESTORE_ALLOWED=false`.
2. Remove `RESTORE_ARTIFACT`, `RESTORE_TARGET_DATABASE_URL`, and `RESTORE_TARGET_CONFIRMATION`.
3. Restore the service config to `railway.backup.json`.
4. Confirm the daily cron remains `17 2 * * *` and restart policy remains `NEVER`.
5. Do not delete the isolated target yet.

## Restored application smoke

Create a disposable application service from the same certified `main` commit and connect only its `DATABASE_URL` to the restored PostgreSQL service.

Keep all action flags disabled:

```text
BRIEF_SCANNER_RUNTIME_ENABLED=false
BRIEF_SCANNER_RUNTIME_MISSION_ENABLED=false
BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED=false
BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED=false
REMINDER_WORKER_ENABLED=false
```

Use synthetic, isolated security values where the application contract requires them. Do not configure a real WhatsApp sender or route real traffic.

Run read-only smoke checks against the disposable service and require:

- `/health`: HTTP 200, `status=ok`, expected version
- `/ready`: HTTP 200, `status=ready`
- PostgreSQL schemas initialized
- migrations current
- schema version current
- database fallback `fail-closed`

## Evidence and cleanup

Record only:

- date and UTC time
- certified source commit
- sanitized artifact and manifest filenames
- positive artifact size
- schema version
- restore result `status=verified`
- restored smoke result
- isolated target deletion confirmation

After evidence is recorded, delete the disposable application and PostgreSQL services. Keep the encrypted production backup according to the retention policy.
