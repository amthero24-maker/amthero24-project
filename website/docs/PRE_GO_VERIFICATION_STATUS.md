# AmtHero24 Website — Pre-GO Verification Status

This file defines how current evidence is established. It intentionally contains no frozen backend SHA, historical Smoke run, historical Certification run, participant identifier, message/document content, secret, or production variable value.

## Live-state rule

Never treat a historical SHA, deployment, Smoke result or Certification result as current launch evidence.

At every preflight, obtain and record the live values in the controlled release record:

- protected GitHub `main` head;
- exact Railway bot and backup deployment SHA/status;
- `/health` and `/ready` results;
- latest protected admin overview and launch-readiness decision;
- latest Production Smoke on the same head;
- latest Deployment Certification on the same head;
- current open production incidents and recovery requirements;
- Closed Beta admission remains disabled immediately before a separately authorized GO.

If any of these sources disagree, use the newest live operational evidence and keep HOLD until the mismatch is explained. A previous PASS cannot authorize a newer commit.

## Website exact-head verification

The website candidate is valid only when all evidence refers to the exact current PR head:

- merge base is current protected `main` and `behind_by=0`;
- final changed-file list is limited to `website/**` plus the website workflow;
- all applicable CI/security/Website CI workflows pass;
- exact committed lockfile installation, production dependency audit and SBOM pass;
- legal/content/lint and strict GO fail-closed checks pass;
- the standalone image builds from the minimized context;
- the exact image runs non-root and emits the tested CSP/security/anti-cache headers;
- no backend admission activation, tracker, remote font/video embed, uncontrolled script, placeholder legal fact or live CTA is introduced;
- no unresolved review thread remains.

The exact candidate SHA belongs in the PR/release evidence, not as a permanent frozen value in this template.

## Current safety defaults

Until every external gate and an explicit owner GO are complete:

- website indexing: disabled;
- live Beta CTA: disabled;
- Closed Beta admission: disabled;
- Wave 1 capacity remains a future hard limit, not an invitation;
- Brief Scanner action runtime and Draft execution: disabled;
- payments and entitlement enforcement: disabled;
- human support activation: disabled;
- website service/domain publication: not inferred from a green build.

## Remaining external prerequisites

A technically green website is not legal approval and not a GO decision. Before publication or admission activation, complete and evidence:

1. Gewerbeanmeldung and factual operator status;
2. exact public operator data and qualified German legal review;
3. processor/subprocessor/retention/third-country-transfer review;
4. operational `info@` and `support@` mail with SPF/DKIM/DMARC evidence;
5. separate Railway website service and Railway-generated ownership values;
6. DNS/TLS/canonical/redirect verification, then DNSSEC after stabilization;
7. resolution of every active serious production incident;
8. fresh backend Smoke and Certification on the exact current `main`;
9. explicit owner authorization in #181.

No document in the website package authorizes publication, admission or real-user contact by itself.
