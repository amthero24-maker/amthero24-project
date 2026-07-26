# AmtHero24 Schema-Aware Recovery

AmtHero24 treats a PostgreSQL restore as incomplete until the restored migration ledger,
latest checksum, and required schema contract match both the encrypted backup manifest and
the running application.

## Backup schema identity

Before `pg_dump` starts, the backup service performs a read-only schema inspection. A
backup is created only when:

- `amthero_schema_migrations` exists
- migration versions are consecutive from 1
- the latest migration version matches the application
- the latest migration checksum matches the application
- the number of ledger entries matches the version sequence
- all required tables and safety-critical columns satisfy the schema contract

The encrypted backup manifest records only:

- `schema_version`
- `schema_checksum`
- `schema_ledger_entries`
- `schema_contract=valid`

These fields describe application structure only. They contain no phone number, phone hash,
message ID, message content, document data, recipient ciphertext, provider payload,
database URL, or credential.

## Restore state machine

A recovery progresses through these states:

1. **Artifact verified**: encrypted artifact name and SHA-256 hashes match the manifest.
2. **Manifest compatible**: the backup schema identity matches the running application.
3. **Restore executed**: `pg_restore` generates SQL and `psql` applies it with
   `ON_ERROR_STOP=on`.
4. **Schema verified**: the target database ledger, checksum, entry count, and schema
   contract are inspected again.
5. **Recovery verified**: restored identity equals the manifest identity exactly.

The restore command returns `status=verified` only after step 5. A completed `psql` process
without post-restore verification is not considered a successful recovery.

## Fail-closed conditions

Backup creation stops before `pg_dump` when schema inspection fails.

Restore stops before `pg_restore` when the manifest:

- lacks schema identity fields
- records an invalid checksum
- has a non-consecutive ledger count
- represents a different application schema version
- has a checksum different from the running application
- reports an invalid schema contract

After SQL execution, recovery still fails when the target:

- lacks the migration ledger
- has an empty or non-consecutive ledger
- has a different latest version or checksum
- has missing required tables or safety-critical columns
- does not exactly match the manifest identity

Failure messages use generic operational codes and do not include SQL, database addresses,
row values, or credentials.

## Legacy backups

Backups created before schema identity was introduced are not accepted by the automated
restore path. They may contain valid data, but the application cannot prove their structural
compatibility safely.

A legacy backup requires a separate isolated investigation:

1. restore only into a disposable PostgreSQL service
2. never point production traffic at that service
3. inspect and migrate the schema with reviewed tooling
4. run the complete application and tenant-isolation tests
5. create a new encrypted schema-bound backup
6. restore that new artifact into another isolated service
7. use only the newly verified artifact for production recovery

Do not edit an old manifest manually to add current schema values.

## Release preflight

When a recent backup is required, Release Preflight now verifies:

- backup age
- encrypted artifact metadata
- artifact integrity hash
- current schema version
- current schema checksum
- complete ledger entry count
- valid schema contract

A recent encrypted artifact with missing or incompatible schema identity blocks release.

## Recovery drill

The weekly and pull-request Schema Recovery Safety workflow:

- creates a fresh PostgreSQL source database
- runs the real migration ledger
- seeds representative encrypted queue, reminder, support, mission, entitlement, provider,
  feedback, and outbound-delivery state
- creates an encrypted custom-format backup
- records schema identity in the manifest
- restores into a separate PostgreSQL database
- verifies the restored schema identity
- compares source and target table sets and aggregate row counts
- proves representative application data remains readable and tenant identifiers remain
  hashed or encrypted

Artifacts uploaded by CI contain only test logs and JUnit results from synthetic databases.

## Production recovery order

1. stop or isolate production writes
2. select a recent encrypted schema-bound backup
3. restore into a separate PostgreSQL service
4. require `status=verified`
5. run application production smoke checks against the restored service
6. run tenant-isolation and delivery-safety checks
7. review queue leases, reminder state, and outbound delivery receipts
8. switch traffic only after explicit operator approval
9. retain the old database until the verified recovery has passed observation

Never run restore directly over the only production database without a separately verified
target and rollback path.
