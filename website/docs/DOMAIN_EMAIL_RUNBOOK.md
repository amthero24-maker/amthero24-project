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
3. In Railway, add `amthero24.de` as the website custom domain. Railway currently provides **both** a CNAME target and a TXT ownership-verification record. Add both exactly as Railway displays them; do not invent either value. A missing TXT record can leave the domain returning 404 even when DNS resolves.
4. For the apex CNAME in Cloudflare, use the Railway-provided target. Cloudflare CNAME flattening can represent the apex safely.
5. When Cloudflare proxying is enabled (orange cloud), follow Railway's current documented requirement: set Cloudflare SSL/TLS encryption mode to **Full**, **not Full (Strict)** and not Flexible. Railway explicitly warns that Full (Strict) does not work as intended for its proxied custom-domain configuration.
6. Keep Cloudflare Universal SSL enabled for the public zone.
7. Wait for Railway domain verification / green state and confirm HTTPS end-to-end before declaring the domain ready.
8. Enable Always Use HTTPS only after HTTPS works end-to-end.
9. Enable DNSSEC after all authoritative DNS is stable; verify the registrar DS chain before recording PASS.
10. Do not enable HSTS preload before the final domain/subdomain inventory, redirect behavior and certificate path are proven safe.

If Railway certificate validation becomes stuck, diagnose DNS/TXT/CAA/DNSSEC first. Railway's current troubleshooting guidance allows temporarily switching the Cloudflare record to DNS-only to complete certificate validation, then restoring the proxy after the Railway domain is green. Do not use this as a random retry loop.

## Canonical redirects

After `https://amthero24.de` is healthy, configure permanent redirects:

- `https://amthero24.com/*` → `https://amthero24.de/$1`
- `https://www.amthero24.com/*` → `https://amthero24.de/$1`
- `https://amthero24.global/*` → `https://amthero24.de/$1`
- `https://www.amthero24.global/*` → `https://amthero24.de/$1`
- `https://www.amthero24.de/*` → `https://amthero24.de/$1`

Use HTTP 301 only after the canonical site is verified. Before that, temporary redirects are safer during setup.

For the final Cloudflare redirect rules, preserve query strings and path suffixes and avoid redirect chains. Verify every host with `curl -I` and a browser from an uncached/private session.

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
- Railway custom-domain CNAME + TXT verification state (values themselves do not need to be copied to GitHub)
- Cloudflare proxy state and SSL/TLS mode = Full
- TLS certificate valid for canonical hostname
- Cloudflare DNSSEC state
- 301 redirect tests for `.com`, `.global`, and `www`
- `info@` inbound/outbound test
- `support@` inbound/outbound test
- SPF result
- DKIM result
- DMARC record and policy
- screenshot/text evidence that indexing and Beta CTA remain disabled until GO
