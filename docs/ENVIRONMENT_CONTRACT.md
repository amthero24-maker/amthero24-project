# AmtHero24 Environment Contract

AmtHero24 treats `.env.example` as the reviewed contract between application code, Railway, GitHub Actions, and operator tooling. It contains variable names and safe defaults only; it never contains production credentials.

## What the guard checks

The `Environment Contract` workflow inspects tracked runtime Python source and compares literal environment-variable accesses with `.env.example`.

It detects accesses through:

- `os.getenv("NAME")`
- `os.environ["NAME"]`
- `os.environ.get`, `setdefault`, and `pop`
- `required_env("NAME")`
- the project's typed and dynamic helpers such as `_flag`, `_int_env`, `_limit`, `_environment_value`, and `assess_secret`

Test-only Python files do not define the production contract. `PORT` is the one explicit external variable because Railway injects it and the start command consumes it outside Python.

The workflow fails when:

- runtime code uses a variable missing from `.env.example`
- `.env.example` documents a variable no longer used by runtime code
- a line is malformed or a name is duplicated
- a sensitive example variable contains a value
- tracked Python source cannot be inspected safely

The JSON report contains variable names and source locations only. It never reads Railway values and never reports runtime credentials.

## Adding a variable

1. Read the variable through a literal name in runtime code.
2. Add the same name to `.env.example` in the relevant section.
3. Use a safe non-secret default when a default is appropriate.
4. Leave keys, tokens, secrets, passwords, and `DATABASE_URL` empty.
5. Add the real value in Railway or GitHub Secrets when required.
6. Run:

```bash
python scripts/validate_environment_contract.py
```

7. Let CI, the secret leak guard, PostgreSQL integration, and recovery drill complete before merge.

Dynamic construction such as `os.getenv(prefix + suffix)` is intentionally unsupported because it cannot be reviewed reliably. Use a literal variable name at the helper call site.

## Removing or renaming a variable

1. Remove or rename every runtime access.
2. Update `.env.example` in the same pull request.
3. Update Railway and GitHub only after the new code is deployed safely.
4. For a rename involving encryption or credentials, follow the dedicated migration or rotation runbook; do not simply delete the old value.
5. Run Release Preflight after the production change.

The guard rejects stale documented variables so operators do not continue maintaining settings that no longer affect the application.

## Production values

`.env.example` is documentation, not a deployment file. Production values belong in Railway variables or GitHub Actions secrets.

Sensitive examples must remain empty, including names ending in:

```text
_KEY
_TOKEN
_SECRET
_PASSWORD
```

The same rule applies to `DATABASE_URL`, `GROQ_API_KEY`, `WHATSAPP_TOKEN`, and `META_APP_SECRET`.

## Current operational groups

The contract covers:

- Groq text, vision, and transcription models
- Meta webhook and WhatsApp delivery
- PostgreSQL storage and fail-closed behavior
- reminder delivery, encryption, templates, and migration
- privacy retention
- plan entitlements and quotas
- abuse protection
- provider telemetry and circuit breaking
- human support security
- production smoke monitoring and release preflight
- encrypted backup and restore tooling
- Railway process startup

Plan limits for `free`, `beta`, `hero`, `family`, and `business` are documented even when enforcement is disabled. Changing a number does not activate payment or enforcement by itself.

## Local validation

Human-readable output:

```bash
python scripts/validate_environment_contract.py
```

Sanitized machine-readable output:

```bash
python scripts/validate_environment_contract.py --json > environment-contract.json
```

Generated reports are ignored by Git and uploaded by CI for 30 days.
