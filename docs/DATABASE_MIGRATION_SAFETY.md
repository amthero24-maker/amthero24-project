# AmtHero24 Database Migration Safety

AmtHero24 applies production PostgreSQL schema changes through one ordered migration
ledger before the application is allowed to become ready.

## Safety model

The production storage policy performs these steps in order:

1. connect to the configured PostgreSQL service
2. acquire a bounded PostgreSQL advisory lock shared by all application replicas
3. create or read `amthero_schema_migrations`
4. reject a database whose recorded version is newer than the running application
5. reject a changed checksum for an already-applied migration
6. apply missing migrations in ascending integer order
7. validate required tables and safety-critical columns
8. release the advisory lock
9. import the historical JSON store once, when present
10. continue application composition and allow `/ready` only when the schema is current

The lock is session-scoped and released explicitly. PostgreSQL also releases it if the
connection closes unexpectedly.

## Ledger privacy boundary

`amthero_schema_migrations` contains only:

- integer migration version
- static migration name
- SHA-256 contract checksum
- application version that first applied the migration
- application timestamp

It contains no phone number, phone hash, message ID, message text, document content,
provider response, ciphertext, token, secret, or database URL.

## Current migration

Version 1 records the existing production schema and ensures the process-owned lease
columns and indexes required by the graceful Railway drain are present.

It is additive and idempotent. It does not delete, rename, rewrite, or backfill user data.

## Adding a future migration

A new schema change must use a new integer version. Never edit the name, checksum contract,
or behavior of an already-deployed migration.

A future migration should:

1. prefer additive nullable columns or new tables
2. avoid long table rewrites during application startup
3. make old and new application versions temporarily compatible during Railway overlap
4. separate destructive cleanup into a later release after the old version is gone
5. add a real PostgreSQL concurrency test
6. add restore coverage when the new table contains durable state
7. update the schema contract and application version

If an old migration checksum changes, startup fails with the safe code
`migration_checksum_mismatch`. Create a new migration instead of changing history.

## Lock timeout

`SCHEMA_MIGRATION_LOCK_TIMEOUT_SECONDS` defaults to 30 seconds and is bounded from 1 to
120 seconds. A replica that cannot acquire the lock within the limit fails startup with
`migration_lock_timeout`; it does not bypass migrations or accept traffic.

Increasing the timeout is appropriate only when another deployment is legitimately
applying a reviewed migration. It must not be used to hide a stuck transaction.

## Fail-closed conditions

Production startup is refused for:

- database schema version newer than the application
- migration name mismatch
- migration checksum mismatch
- incomplete required schema contract
- migration lock timeout
- PostgreSQL or DDL failure

Schema incompatibility never activates JSON fallback, even when emergency fallback was
explicitly enabled. Falling back in that situation would hide a deployment error and split
new writes from the durable database.

## Readiness and certification

`/ready` exposes only:

- `database_schema_migrations`: `current`, `unverified`, `not-required`, or
  `not-applicable`
- `database_schema_version`: an integer

Production smoke and deployment certification require `current` and a version of at least
1. No migration names, checksums, lock identifiers, SQL, database addresses, or row data are
returned.

## Incident response

When migration startup fails:

1. keep the new deployment out of traffic
2. inspect the safe failure code and GitHub Actions migration diagnostics
3. verify the deployed application version
4. verify which migration versions exist in the ledger
5. do not delete ledger rows or alter checksums to force startup
6. do not enable JSON fallback for schema incompatibility
7. fix the migration in a new version or deploy the last compatible application
8. restore only through the encrypted restore procedure when data recovery is required

The dedicated **Database Migration Safety** workflow validates policy, checksum behavior,
lock timeout, two-replica serialization, schema-ahead rejection, and critical-column
validation against PostgreSQL 16.
