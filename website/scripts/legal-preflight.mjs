const publicRelease = process.env.NEXT_PUBLIC_SITE_INDEXABLE === 'true' || process.env.LEGAL_PRODUCTION_READY === 'true';
const required = ['LEGAL_OPERATOR_NAME','LEGAL_TRADING_NAME','LEGAL_OPERATOR_STATUS','LEGAL_COUNTRY','LEGAL_EMAIL_INFO','LEGAL_EMAIL_SUPPORT'];
const missing = required.filter((key) => !process.env[key] || /\[.*\]/.test(process.env[key]));
if (publicRelease) {
  const approvals = ['LEGAL_PROVIDER_DPAS_CONFIRMED','LEGAL_META_TERMS_CONFIRMED','LEGAL_RAILWAY_REGION_CONFIRMED','LEGAL_WEBSITE_REVIEW_CONFIRMED','LEGAL_VSBG_REVIEW_CONFIRMED','DOMAIN_PRIMARY_TLS_CONFIRMED','DOMAIN_REDIRECTS_CONFIRMED','EMAIL_INBOUND_ROUTING_CONFIRMED','EMAIL_OUTBOUND_AUTH_CONFIRMED','AI_TRANSPARENCY_REVIEW_CONFIRMED'];
  const blocked = approvals.filter((key) => process.env[key] !== 'true');
  if (missing.length || blocked.length || !process.env.LEGAL_SERVICE_ADDRESS) {
    console.error('Public legal preflight blocked', { missing, blocked, serviceAddress: Boolean(process.env.LEGAL_SERVICE_ADDRESS) });
    process.exit(1);
  }
}
console.log('Legal preflight PASS for safe pre-GO/noindex mode.');
