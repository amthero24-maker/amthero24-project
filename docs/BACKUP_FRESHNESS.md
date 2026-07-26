# AmtHero24 Backup Freshness

AmtHero24 records whether a verified encrypted PostgreSQL backup exists and whether it is
still inside the controlled-Beta recovery point objective. The checkpoint is operational
metadata only; it does not contain an artifact name, storage path, database URL, user
identifier, message, document, ciphertext, or credential.

## Safe rollout order

1. Deploy AmtHero24 v4.8.0 with `BACKUP_FRESHNESS_ENFORCEMENT_ENABLED=false`.
2. Allow migration version 2 to create `backup_operational_state`.
3. Configure a dedicated Railway backup service with the production private
   `DATABASE_URL`, `BACKUP_ENCRYPTION_KEY`, and a persistent volume mounted at
   `BACKUP_OUTPUT_DIR`.
4. Run `python scripts/postgres_backup.py` once.
5. Confirm the protected admin overview reports a fresh encrypted backup using the current
   schema version.
6. Confirm the launch report is otherwise ready.
7. Set `BACKUP_FRESHNESS_ENFORCEMENT_ENABLED=true`.
8. Continue Beta growth only while the checkpoint remains inside the configured RPO.

The first deployment stays observe-only so an empty new checkpoint cannot unexpectedly
stop an already-running service. Missing or stale backups still appear as launch warnings.

## Railway backup service

Use a separate Railway service or cron job, not the web process. The service needs:

```text
DATABASE_URL=<private PostgreSQL URL>
BACKUP_ENCRYPTION_KEY=<strong Fernet key>
BACKUP_OUTPUT_DIR=/backups
BACKUP_KEEP_COUNT=14
```

The `/backups` directory must be a persistent volume. A successful command:

- validates the current migration ledger and schema contract
- creates a custom-format PostgreSQL dump
- encrypts the artifact before moving it into the volume
- writes a schema-bound manifest atomically
- records the successful checkpoint only after the artifact and manifest are complete
- rotates old artifacts only after the new artifact exists

The command exits non-zero when artifact creation or checkpoint recording fails.

## RPO thresholds

Defaults:

```text
BACKUP_WARNING_AFTER_HOURS=30
BACKUP_BLOCK_AFTER_HOURS=48
```

Thresholds are bounded in code. At the warning threshold, the launch report asks the
operator to inspect the cron schedule and persistent volume. At the block threshold:

- observe-only mode remains a warning
- enforcement mode blocks controlled-Beta expansion

Backup freshness is deliberately not part of `/ready` liveness. Users should not lose an
urgent WhatsApp service merely because a backup job is late; instead, invitations and
release expansion stop until recovery protection is restored.

## Failed attempts

A failed attempt records only:

- attempt timestamp
- generic safe failure code
- failed status

It preserves the last verified successful backup timestamp and schema identity. The launch
report warns immediately when the latest attempt failed, even while the last successful
artifact is still fresh.

Safe examples include `pg_dump_connection_failed`, `pg_dump_authentication_failed`, and
`backup_checkpoint_write_failed`. Raw stderr, connection details, paths, and exception
messages are never stored in the checkpoint.

## Privacy boundary

The database checkpoint stores:

- fixed scope `production`
- last attempt, success, and failure timestamps
- generic status and failure code
- artifact SHA-256 and byte size
- schema version and migration checksum
- encrypted flag

Only aggregate state is returned by the protected admin overview. Artifact SHA-256 remains
inside PostgreSQL and is not exposed through the API because operators do not need it for
routine RPO decisions.

## Incident response

When freshness warns or blocks:

1. pause new Beta invitations and nonessential releases
2. inspect the Railway backup service status
3. verify the persistent volume is mounted and writable
4. verify PostgreSQL client/server compatibility
5. verify the backup encryption key is configured
6. run one backup manually
7. confirm the new checkpoint is encrypted and uses the current schema version
8. run the schema-aware recovery drill when the failure involved artifact integrity or
   schema compatibility
9. re-enable expansion only after the launch report returns ready

Do not clear the checkpoint, alter timestamps, or disable enforcement merely to hide a
failed backup job.

## Migration history

Version 1's deployed checksum is frozen permanently. Migration version 2 owns only the
backup freshness table contract. Future migrations must receive their own independent
contract and checksum; extending the current schema must never rewrite historical
migration checksums.
