# AmtHero24 Online Migration Policy

AmtHero24 runs application migrations during Railway startup while the previous container
may still serve traffic. Startup migrations must therefore be compatible with both the old
and new application versions.

## Expand before contract

Every schema evolution uses at least two releases:

1. **Expand release**: add optional structures that old code can ignore.
2. **Application release**: deploy code that can use the new and old structures safely.
3. **Observation period**: verify delivery, queue, reminder, privacy, and database metrics.
4. **Contract operation**: remove obsolete structures only through a separate reviewed
   maintenance procedure after every old application instance is gone.

Contract operations never run in the application startup migration registry.

## Allowed startup SQL

The online-safe executor accepts one literal, idempotent statement at a time:

- `CREATE TABLE IF NOT EXISTS ...`
- `CREATE INDEX IF NOT EXISTS ...`
- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`

Added columns must remain nullable and may not add `UNIQUE`, `REFERENCES`, `GENERATED`, or
identity behavior during startup. New tables may define their own initial constraints
because no existing rows require rewriting.

## Rejected startup operations

The runtime executor and static AST scanner reject:

- `DROP` of tables, columns, indexes, or constraints
- `RENAME`
- `TRUNCATE`
- `INSERT`, `UPDATE`, or `DELETE` data migrations
- `ALTER COLUMN`, type changes, and `SET NOT NULL`
- unique-index creation
- explicit table locks
- `VACUUM`, `REINDEX`, or `CLUSTER`
- multiple SQL statements in one call
- SQL assembled dynamically or with runtime interpolation
- direct `.execute()` calls outside `OnlineMigrationExecutor`
- non-consecutive or duplicate migration versions
- startup migrations whose phase is not `expand`
- legacy bootstrap flags after migration version 1

A rejected runtime statement raises only the safe operational code
`unsafe_online_migration_sql`; SQL text is not added to logs or readiness output.

## Writing a new migration

A future migration function should receive `store` and `executor`, use a new consecutive
integer version, and contain literal SQL only.

```python
def _apply_feature_v2(store, executor):
    executor.execute(
        "ALTER TABLE hero_users "
        "ADD COLUMN IF NOT EXISTS feature_state JSONB"
    )
    return ()

_MIGRATIONS = (
    # historical entries remain unchanged
    MigrationSpec(
        version=2,
        name="feature_state_v2",
        checksum=_SCHEMA_V2_CHECKSUM,
        apply=_apply_feature_v2,
        phase="expand",
    ),
)
```

The migration must also update the schema contract, tests, and application version. Never
edit an already-applied migration name, checksum, phase, or function.

## Data backfills

Large or user-data backfills do not run during startup. They require a separate bounded job
with:

- explicit operator invocation
- resumable cursor or batch key
- small transactions
- rate limits and statement timeouts
- aggregate-only progress reporting
- no message content or phone identifiers in logs
- cancellation and restart safety
- an encrypted backup and restore proof before destructive follow-up

The application must remain compatible while a backfill is partially complete.

## Static CI evidence

The **Database Migration Safety** workflow:

1. parses `database_migrations.py` with Python AST
2. validates registry order and expand-only phases
3. requires migration SQL to be literal and routed through the executor
4. classifies every statement without returning its text
5. runs policy fixtures for destructive, dynamic, and non-idempotent changes
6. runs real PostgreSQL tests proving rejected SQL never reaches the database

The uploaded `migration-policy.json` contains only rule names, source file, line number, and
generic messages. It contains no SQL statements, database addresses, row values, or
credentials.

## Contract operations

A future contract procedure must be deliberately separate from startup and should require:

- proof that no old application version remains active
- a recent encrypted backup and successful restore drill
- explicit operator approval
- a maintenance window when locking risk exists
- a preview of affected schema objects and row counts without exposing row contents
- an independently tested rollback or restore path

Until that procedure exists and passes its own gates, obsolete columns and tables remain in
place. Storage cost is preferable to an unsafe rolling deployment.
