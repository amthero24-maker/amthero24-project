# AmtHero24 Supply-Chain Security

This runbook defines how Python dependencies and GitHub Actions are reviewed before they reach production.

## Dependency separation

The repository uses three manifests:

```text
requirements.txt
requirements-dev.txt
requirements-security.txt
```

`requirements.txt` is the production runtime only. Test frameworks and audit tools are intentionally excluded so Railway installs a smaller dependency set.

`requirements-dev.txt` includes the production runtime and adds only development/test tooling.

`requirements-security.txt` includes the production runtime and adds the vulnerability-audit tool used by the dedicated security workflow.

All direct dependencies must have both a minimum version and an exclusive upper bound. Direct URLs, VCS references, editable installs, local file dependencies, index overrides, and duplicate normalized package names are rejected by policy.

## Network-free policy validation

Run locally:

```bash
python scripts/validate_dependency_policy.py
```

The validator checks:

- production does not include pytest or audit tooling
- development and security manifests include `requirements.txt` exactly once
- direct dependencies are bounded
- direct URLs, VCS references, local paths, editable installs, and installer flags are absent
- duplicate dependency names are absent
- workflows do not use `pull_request_target`
- external GitHub Actions use explicit non-floating references

The validator is deterministic and does not contact package registries.

## Vulnerability audit and SBOM

The `Supply Chain Security` workflow runs:

- on relevant pull requests
- after relevant changes reach `main`
- every Monday
- manually through `workflow_dispatch`

It performs:

1. dependency/workflow policy validation
2. installation of the production and audit dependency set
3. `pip check` compatibility validation
4. known-vulnerability auditing of `requirements.txt`
5. CycloneDX JSON SBOM generation for production requirements
6. capture of the resolved audit environment

The workflow fails when policy validation, dependency compatibility, vulnerability auditing, or SBOM generation fails.

The `supply-chain-evidence` artifact is retained for 30 days and may contain package names and versions. It must not contain application credentials or user data.

## Dependabot

Dependabot checks Python dependencies and GitHub Actions every Monday. It creates bounded pull requests rather than modifying `main` directly. Every update must pass the normal CI, PostgreSQL integration, recovery drill, and supply-chain workflow before merge.

Do not auto-merge dependency updates solely because they are patch releases. Review behavior changes, transitive dependency changes, and provider compatibility.

## Handling a vulnerability

1. Confirm the failing package and vulnerability identifier in the workflow artifact.
2. Check whether a fixed version exists inside the repository's current upper bound.
3. Update the smallest safe version range necessary.
4. Run all CI and production-readiness checks.
5. If no fix exists, document the exposure and compensating controls in a GitHub issue; do not silently ignore the advisory.
6. After deployment, update `EXPECTED_APP_VERSION` and run `Release Preflight`.

Never use `pip-audit --fix` directly against production or commit an automatically rewritten dependency set without review.

## GitHub Actions references

Floating refs such as `@main`, `@master`, and `@latest` are prohibited. Versioned refs are accepted by the repository policy and monitored by Dependabot. High-risk workflows should later move to reviewed full commit SHAs when the exact upstream commit is verified.
