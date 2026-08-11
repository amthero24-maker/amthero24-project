# AmtHero24 — Final Pre-GO Package Status

## Technical package

The standalone website package is complete on `feat/pre-go-launch-package-205` and remains isolated from the certified backend baseline `555b69c13d1af136090a4a210b0e75a55b57caf9`.

Included:

- German-first Next.js/Tailwind landing page;
- six MVP journey sections with original local 15-second demo videos and posters;
- clear problem/solution, three-step usage flow, before/after examples, trust section, Germany positioning, FAQ, and disabled Closed Beta CTA;
- German draft pages for Impressum, Datenschutz, AGB, Kontakt, Widerruf/Teilnahmebeendigung, Closed Beta notice, cookie settings, and accessibility;
- Cloudflare/Railway/domain/email runbooks for `amthero24.de`, `.com`, and `.global`;
- four campaign-video scripts and ten image-generation prompts;
- legal/operator data form, source notes, execution estimates, deployment instructions, final GO checklist, and first-five-user monitoring plan;
- permanent tracker-free, legal-lock, claims, lint, build, and local-asset checks.

## Safety state

- website indexing: disabled by default;
- Closed Beta CTA: disabled by default;
- Closed Beta admission: disabled;
- Wave 1 hard capacity after a separate explicit owner GO: 5;
- Brief Scanner Runtime, Draft execution, payments, entitlement enforcement, and human support: disabled;
- no external trackers, analytics, remote fonts, remote scripts, or CDN dependencies;
- no backend runtime or production configuration changes in this package.

## Remaining external gates

The package is not a GO and is not ready for public indexing until all of the following are completed and recorded:

1. factual operator data, including legal form and a serviceable German address;
2. qualified German legal review of the final texts and actual data flows;
3. processor agreements, subprocessors, and international-transfer review for Meta/WhatsApp, Groq, Railway, and Cloudflare;
4. separate Railway website service deployment;
5. Cloudflare DNS/TLS/DNSSEC, canonical-domain, and 301 redirect verification;
6. inbound email routing plus outbound SPF, DKIM, and DMARC verification;
7. final live checks for the canonical site and the certified bot endpoints;
8. explicit owner authorization recorded in #181.

Until those gates are complete, `LEGAL_PRODUCTION_READY`, indexing, the CTA, and admission must remain disabled.
