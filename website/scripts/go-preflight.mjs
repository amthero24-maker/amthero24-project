const checks = {
  legal: process.env.LEGAL_PRODUCTION_READY === 'true',
  indexing: process.env.NEXT_PUBLIC_SITE_INDEXABLE === 'true',
  betaCta: process.env.NEXT_PUBLIC_BETA_CTA_ENABLED === 'true',
  tls: process.env.DOMAIN_PRIMARY_TLS_CONFIRMED === 'true',
  redirects: process.env.DOMAIN_REDIRECTS_CONFIRMED === 'true',
  inboundMail: process.env.EMAIL_INBOUND_ROUTING_CONFIRMED === 'true',
  outboundMail: process.env.EMAIL_OUTBOUND_AUTH_CONFIRMED === 'true',
  betaNotice: process.env.BETA_NOTICE_OWNER_APPROVED === 'true',
  aiTransparency: process.env.AI_TRANSPARENCY_REVIEW_CONFIRMED === 'true'
};
const failed = Object.entries(checks).filter(([,ok]) => !ok).map(([name]) => name);
if (failed.length) {
  console.error(`GO blocked: ${failed.join(', ')}`);
  process.exit(1);
}
console.log('Website GO preflight PASS. Backend admission still requires a separate production authorization.');
