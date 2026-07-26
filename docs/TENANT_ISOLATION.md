# AmtHero24 Tenant Isolation

AmtHero24 treats each WhatsApp sender as an independent tenant. User-scoped reads,
updates, exports, cancellations, and deletion must always be filtered by the requesting
user's one-way phone hash. A record identifier alone is never sufficient for a
user-facing operation.

## Protected layers

The automated contract covers:

- profile and temporary conversation context
- inbound operational messages
- memory consent history
- missions and mission updates
- reminder listing and cancellation
- pending document actions
- plan assignment and usage counters
- abuse-rate windows and blocks
- human-support tickets
- privacy export and full deletion

Reminder and human-support recipients remain reversibly encrypted only because the
service must contact the same user later. Their user-facing views never expose the
ciphertext or another tenant's contact.

## Required invariants

1. Two different phone numbers produce independent profiles and repository results.
2. Listing or exporting tenant A never contains tenant B markers or records.
3. Completing, cancelling, or deleting tenant A never mutates tenant B.
4. Concurrent operations on A and B remain isolated under PostgreSQL row locking.
5. Full deletion removes every A-linked row while every seeded B-linked row survives.
6. Raw phone numbers are not stored in profiles, inbound text, missions, counters, or
   other hash-addressed tables.
7. PostgreSQL integration tests use only synthetic numbers in an ephemeral CI database.

## CI enforcement

The normal test job runs the atomic JSON boundary contract. The PostgreSQL integration
job runs the same boundary against the final production composition, including every
privacy-deletion wrapper loaded by `runtime_health`.

Any new user-linked repository must be added to both the production deletion chain and
the tenant-isolation test table list before merge. Aggregate admin reports may span
users, but must remain aggregate-only unless a separately authenticated operational
queue explicitly requires encrypted contact recovery.
