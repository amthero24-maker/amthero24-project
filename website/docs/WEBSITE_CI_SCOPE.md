# Permanent Website CI Scope

The permanent `Website CI` workflow validates only the standalone landing-site package and must not activate or modify the certified backend.

Required checks:

- exact dependency installation from `website/package-lock.json`;
- legal publication lock;
- required legal routes, local media, AI transparency, and tracker/CDN rejection;
- ESLint;
- standalone Next.js production build;
- strict GO remains blocked until factual operator, legal-review, domain, and production-preflight prerequisites are complete;
- an enabled Closed Beta CTA must have an actionable HTTPS WhatsApp target on the approved `wa.me` or `api.whatsapp.com` hosts; missing or unrelated targets fail closed;
- website changes remain isolated from backend runtime and Closed Beta admission controls.

Temporary bootstrap and repair workflows are not part of the final branch tree.
