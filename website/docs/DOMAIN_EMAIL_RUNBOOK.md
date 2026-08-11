# AmtHero24 Domain, TLS and Mail Runbook

Status: pre-GO. This runbook does not authorize public indexing or Closed Beta admission.

## Canonical domain

Primary: `amthero24.de`
Redirect-only: `amthero24.com`, `amthero24.global`

## Railway website service

Create a new Railway service for the website. Do not reuse, replace, or mutate the certified bot service.

- repository: `amthero24-maker/amthero24-project`
- root directory: `/website`
- config-as-code path: `/website/railway.json`
- health check: `/api/health`
- production domain target: `amthero24.de`
- keep the bot's current production service, variables, deployment and domain untouched

Deploy the website first on a Railway-generated hostname. Verify build, `/api/health`, noindex, disabled CTA and legal fail-closed state before attaching public domains.

## Cloudflare DNS and TLS

1. Add all three domains to the same controlled Cloudflare account or otherwise ensure the same DNS policy is applied.
2. Import/verify DNS records; remove stale records only after confirming they are not in use.
3. For `amthero24.de`, add the exact CNAME target Railway provides for the website custom domain. Do not invent the target.
4. Keep Cloudflare proxy mode aligned with Railway's current custom-domain guidance. Confirm Railway has issued/validated TLS before declaring the domain ready.
5. Set SSL/TLS mode to Full (strict) after origin certificate validation is confirmed.
6. Enable Always Use HTTPS only after HTTPS works end-to-end.
7. Enable DNSSEC after all authoritative DNS is stable; record the DS status.
8. Do not enable HSTS preload before the final domain and subdomain inventory is proven safe.

## Canonical redirects

After `https://amthero24.de` is healthy, configure permanent redirects:

- `https://amthero24.com/*` → `https://amthero24.de/$1`
- `https://www.amthero24.com/*` → `https://amthero24.de/$1`
- `https://amthero24.global/*` → `https://amthero24.de/$1`
- `https://www.amthero24.global/*` → `https://amthero24.de/$1`
- `https://www.amthero24.de/*` → `https://amthero24.de/$1`

Use HTTP 301 only after the canonical site is verified. Before that, temporary redirects are safer during setup.

Verify with curl/browser that the redirect is one hop, HTTPS remains valid, paths are preserved, and no redirect loop exists.

## Temporary noindex gate

Until legal/operator review is complete:

- `NEXT_PUBLIC_SITE_INDEXABLE=false`
- robots response must block indexing
- sitemap must not advertise a public-ready site
- do not remove the gate merely because DNS works

The indexability flag becomes eligible for `true` only after the final GO checklist explicitly passes.

## Mailboxes

Required public aliases on `amthero24.de`:

- `info@amthero24.de` — general/legal contact
- `support@amthero24.de` — beta and technical support

Choose a mail provider before creating MX records. Do not mix MX records from multiple providers unless deliberately supported.

### Inbound verification

- MX records match the selected provider exactly
- both aliases receive external test mail
- replies originate from the intended domain
- SPF alignment is confirmed
- DKIM signatures validate
- DMARC is present

### Recommended staged DMARC rollout

Start with a monitoring policy after SPF/DKIM are proven, review aggregate reports, then tighten to quarantine/reject when all legitimate senders are known. Do not publish a strict reject policy before identifying every authorized sender.

Example shape only — replace provider-specific values with verified records:

- SPF: provider-authorized `v=spf1 ... -all`
- DKIM: provider-issued selector records
- DMARC monitoring: `_dmarc.amthero24.de TXT "v=DMARC1; p=none; rua=mailto:<verified-report-mailbox>"`

Do not publish placeholder DNS values.

## Evidence to record before GO

- Railway website deployment ID/SHA
- custom-domain verification state
- TLS certificate valid for canonical hostname
- Cloudflare DNSSEC state
- 301 redirect tests for `.com`, `.global`, and `www`
- `info@` inbound/outbound test
- `support@` inbound/outbound test
- SPF result
- DKIM result
- DMARC record and policy
- screenshot/text evidence that indexing and Beta CTA remain disabled until GO
