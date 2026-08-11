# AmtHero24 Closed Beta — Final GO / HOLD / STOP Checklist

This checklist authorizes nothing by itself. The owner must explicitly issue GO only after every required item is evidenced.

## A. Repository and certified backend

- [ ] `main` SHA recorded and matches the certified production deployment.
- [ ] No unreviewed backend/runtime change is bundled with the website rollout.
- [ ] Required branch protection / CI governance remains active.
- [ ] Production Smoke = PASS.
- [ ] Deployment Certification = PASS / stable 3 of 3.
- [ ] `/health` = 200 / ok.
- [ ] `/ready` = 200 / ready.
- [ ] `/admin/launch-readiness` = ready.
- [ ] `/admin/overview` confirms admission disabled and capacity configuration remains fail-closed before GO.

## B. Website technical readiness

- [ ] Website CI = PASS on the exact candidate SHA.
- [ ] `npm ci`, lint and `next build` = PASS.
- [ ] Docker image build = PASS.
- [ ] Website `/api/health` = 200 on Railway-generated hostname.
- [ ] No analytics, marketing trackers, remote fonts, external video embeds or uncontrolled third-party scripts.
- [ ] `NEXT_PUBLIC_SITE_INDEXABLE=false` until legal publication is approved.
- [ ] `NEXT_PUBLIC_BETA_CTA_ENABLED=false` until explicit owner GO.
- [ ] Strict `go:check` remains BLOCKED before final external facts are supplied.

## C. Legal publication

- [ ] Operator legal form/status confirmed.
- [ ] Serviceable German postal address confirmed.
- [ ] Register/VAT/supervisory fields reviewed for applicability and completed when applicable.
- [ ] VSBG statement decided based on actual business status.
- [ ] `info@amthero24.de` operational.
- [ ] `support@amthero24.de` operational.
- [ ] Impressum final review completed.
- [ ] Datenschutzerklärung matches actual production processing.
- [ ] AGB / Beta terms final review completed.
- [ ] Widerruf / free-beta termination wording reviewed for the actual commercial model.
- [ ] Cookie/privacy settings accurately state the technologies actually deployed.
- [ ] AI transparency wording approved.
- [ ] Final Beta Notice version from Issue #181 approved by owner.
- [ ] Qualified German legal review recorded.

## D. Processors and privacy facts

- [ ] Meta/WhatsApp terms, DPA/roles and international-transfer basis confirmed.
- [ ] Railway processing terms, subprocessors and actual region confirmed.
- [ ] Groq processing terms and actual retention/ZDR configuration confirmed.
- [ ] Cloudflare processing role confirmed.
- [ ] Email provider processing terms confirmed.
- [ ] Subprocessor list complete and factual.
- [ ] Retention statements match runtime behavior.
- [ ] Hero Memory consent/export/delete statements match runtime behavior.

## E. Domains and email

- [ ] `amthero24.de` attached to the separate website service.
- [ ] Valid TLS for `amthero24.de`.
- [ ] `www.amthero24.de` redirect verified.
- [ ] `amthero24.com` and `www` redirect 301 to canonical `.de` in one hop.
- [ ] `amthero24.global` and `www` redirect 301 to canonical `.de` in one hop.
- [ ] Path/query behavior checked where expected.
- [ ] No redirect loops.
- [ ] Cloudflare DNSSEC enabled and verified after DNS stabilization.
- [ ] SPF passes for authorized outbound mail.
- [ ] DKIM passes for authorized outbound mail.
- [ ] DMARC record present; policy appropriate to the verified sender inventory.

## F. Closed Beta Wave 1 controls

- [ ] Wave 1 maximum simultaneous admitted users = 5.
- [ ] Admission currently DISABLED immediately before GO.
- [ ] Opt-in is mandatory and versioned.
- [ ] Beta opt-in is separate from Hero Memory consent.
- [ ] No public/self-service signup path bypasses admission controls.
- [ ] Brief Scanner Runtime remains disabled.
- [ ] Draft execution remains disabled.
- [ ] Payments remain disabled.
- [ ] Entitlement enforcement remains disabled unless separately approved.
- [ ] Human support activation remains disabled unless separately approved.

## G. First-five monitoring

For the first five admitted users, monitor only privacy-safe/aggregated operational evidence:

- inbound processing success/failure;
- outbound delivery success/failure;
- reminder due-unsent count and retry behavior;
- duplicate/idempotency evidence;
- provider timeout/error rates;
- document-processing failures without retaining document contents;
- user confusion / conversation-quality incidents using sanitized summaries;
- consent/export/delete correctness;
- CPU, memory, restart/crash evidence and replicas;
- any security/privacy anomaly.

Do not log raw phone numbers, message/document contents or secrets to monitoring/CI.

## H. STOP conditions

Immediate HOLD/STOP expansion on any of:

- privacy leakage or cross-user data;
- webhook/signature regression;
- database consistency or tenant-isolation problem;
- unreadable reminder encryption;
- persistent outbound failure or dangerous duplication;
- broken reminder delivery with due-unsent growth;
- uncontrolled runtime action;
- severe document hallucination causing unsafe action;
- material legal/privacy mismatch between published text and actual processing.

Response: stop expansion → preserve active green deployments → capture sanitized evidence → isolate root cause → smallest focused PR → CI → deploy → Smoke/Certification → controlled retest.

## I. Exact GO sequence

Only after A–H pass:

1. Record candidate website SHA and certified backend `main` SHA.
2. Set verified legal/domain/email readiness flags for the website only.
3. Deploy/verify canonical website while keeping admission disabled.
4. Confirm public legal pages, TLS, redirects and no placeholder content.
5. If public launch is intended, enable indexing separately; indexing is not required to admit the private Wave 1 cohort.
6. Owner issues explicit GO for Closed Beta Wave 1.
7. Only then enable the existing admission mechanism with hard capacity 5 and mandatory opt-in.
8. Admit users gradually, never exceeding 5 simultaneous admitted users.
9. Re-run production health/readiness and monitor the first-five evidence continuously during the initial wave.

A GO for the website is not automatically a GO for admission, runtime actions, payments, Draft execution or wider Beta expansion.
