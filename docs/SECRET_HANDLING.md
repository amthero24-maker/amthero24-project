# AmtHero24 Credential Handling

This runbook defines how secrets are stored, reviewed, rotated, and handled if accidental exposure is suspected.

## Storage rules

Production credentials belong only in Railway variables, GitHub Actions secrets, Meta, Groq, or another approved secret manager. They must never be committed to GitHub, pasted into issues, included in screenshots, written to application logs, or placed in uploaded workflow artifacts.

Examples of protected values include:

```text
GROQ_API_KEY
WHATSAPP_TOKEN
META_APP_SECRET
VERIFY_TOKEN
ADMIN_API_TOKEN
REMINDER_ENCRYPTION_KEY
SUPPORT_API_TOKEN
SUPPORT_ENCRYPTION_KEY
BACKUP_ENCRYPTION_KEY
DATABASE_URL
```

Use `.env.example` only for empty values and non-secret defaults. Local `.env` files and common key, certificate, database-dump, backup, and security-report artifacts are excluded by `.gitignore`.

## Automated leak guard

The `Secret Leak Guard` workflow runs on pull requests, pushes to `main`, weekly schedules, and manual requests. It scans tracked text files only and skips binary or oversized files.

The scanner detects:

- private-key headers
- Groq/OpenAI-style keys
- GitHub personal and fine-grained tokens
- Stripe live keys
- Slack tokens
- Google API keys
- long Meta access tokens
- database URLs containing embedded passwords
- committed non-placeholder values for AmtHero24's sensitive environment-variable names

The report never contains the matched value. Each finding contains only:

- repository-relative file
- line number
- detection rule
- short SHA-256 fingerprint
- generic remediation message

The fingerprint can correlate the same leaked value across findings without revealing it.

## Safe fixtures

Tests and CI may use explicit placeholders such as `isolated-*`, `*-ci-*`, `*-recovery-*`, `example-*`, or localhost PostgreSQL credentials. These values must not grant access to any external service.

Do not weaken a detector merely to make a real credential pass. Replace the value with a clearly fake fixture or move it to a GitHub secret reference.

## Local scan

Run before pushing sensitive configuration changes:

```bash
python scripts/scan_repository_secrets.py
```

For a machine-readable sanitized report:

```bash
python scripts/scan_repository_secrets.py --json > secret-scan-report.json
```

## Suspected exposure response

Treat a committed credential as compromised even if the commit was quickly removed.

1. Revoke or rotate the credential at its provider immediately.
2. Pause affected production functions if continued access could expose user data or send messages.
3. Search GitHub commits, pull requests, Actions logs, artifacts, issues, and screenshots for the same fingerprint or value.
4. Replace the secret in Railway and GitHub with a newly generated independent value.
5. Redeploy and run `Release Preflight` plus the production monitor.
6. Verify reminders, support encryption, backups, Meta signatures, and PostgreSQL connectivity as applicable.
7. Document the incident without copying the secret into the incident record.
8. Consider Git history rewriting only after rotation; history rewriting does not make an exposed credential safe again.

## Key separation

Do not reuse one credential for multiple purposes. In particular:

- never use `WHATSAPP_TOKEN` as an encryption key
- keep reminder, support, backup, and admin secrets independent
- keep GitHub operator tokens separate from production application secrets
- rotate an old reminder key only after atomic re-encryption and a verified post-migration backup

## Artifact review

Security artifacts may contain package names, versions, file paths, rule names, and fingerprints. They must not contain phone numbers, messages, document contents, response bodies, authorization headers, production URLs with credentials, or plaintext keys.
