import fs from 'node:fs';
const page = fs.readFileSync(new URL('../app/page.tsx', import.meta.url), 'utf8');
const layout = fs.readFileSync(new URL('../app/layout.tsx', import.meta.url), 'utf8');
const legal = fs.readFileSync(new URL('../app/[legal]/page.jsx', import.meta.url), 'utf8');
const beta = fs.readFileSync(new URL('../app/beta/page.tsx', import.meta.url), 'utf8');
const health = fs.readFileSync(new URL('../app/api/health/route.js', import.meta.url), 'utf8');
const betaCta = fs.readFileSync(new URL('../lib/beta-cta.ts', import.meta.url), 'utf8');
const legalOperator = fs.readFileSync(new URL('../docs/LEGAL_OPERATOR_COMPLETION.md', import.meta.url), 'utf8');
const retiredPreGewerbe = fs.readFileSync(new URL('../docs/PRE_GEWERBE_VALIDATION_SCORECARD.md', import.meta.url), 'utf8');
const preGoStatus = fs.readFileSync(new URL('../docs/PRE_GO_VERIFICATION_STATUS.md', import.meta.url), 'utf8');
const packageStatus = fs.readFileSync(new URL('../docs/FINAL_PRE_GO_PACKAGE_STATUS.md', import.meta.url), 'utf8');
const tools = ['Brief Scanner','Termin Assistance','Kündigung','Vertrags-Check','Geld zurück','Nachrichten & E-Mails'];
const routes = ['impressum','datenschutz','agb','widerruf','kontakt','beta','cookie-einstellungen','barrierefreiheit'];
for (const item of tools) if (!page.includes(item)) throw new Error(`Missing MVP tool: ${item}`);
for (const route of routes) if (!legal.includes(route)) throw new Error(`Missing legal route: ${route}`);
if (!layout.includes('href="#main"')) throw new Error('Global skip link must target main content');
if (!page.includes('id="main"')) throw new Error('Home page main target missing');
if (!legal.includes('id="main"')) throw new Error('Legal page main target missing');
if (!beta.includes('id="main"')) throw new Error('Beta page main target missing');
if (!health.includes('export const dynamic = "force-dynamic"')) throw new Error('Website health route must remain dynamic');
if (!health.includes('"Cache-Control"') || !health.includes('no-store')) throw new Error('Website health route must prevent cache reuse');
if (!health.includes('Pragma: "no-cache"') || !health.includes('Expires: "0"')) throw new Error('Website health route legacy anti-cache headers missing');
if (!page.includes('resolveBetaCtaUrl')) throw new Error('Landing page must use shared Beta CTA validation');
if (!health.includes('Boolean(resolveBetaCtaUrl())')) throw new Error('Health must report actionable Beta CTA readiness');
if (health.includes('NEXT_PUBLIC_BETA_CTA_ENABLED')) throw new Error('Health must not infer Beta CTA readiness from the flag alone');
if (!betaCta.includes('NEXT_PUBLIC_BETA_CTA_ENABLED') || !betaCta.includes('NEXT_PUBLIC_BETA_CTA_URL')) throw new Error('Shared Beta CTA environment boundary missing');
if (!betaCta.includes('url.hostname === "wa.me"') || !betaCta.includes('url.hostname === "api.whatsapp.com"')) {
  throw new Error('Shared Beta CTA validation must restrict targets to approved WhatsApp hosts');
}
if (!page.includes('href={betaCtaUrl}')) throw new Error('Enabled Beta CTA must be an actionable link');
if (!page.includes('<button className="cta" disabled>')) throw new Error('Disabled pre-GO CTA boundary missing');
if (!page.includes('KI-gestützter')) throw new Error('AI transparency copy missing');
if (!page.includes('5 Sprachen')) throw new Error('Language value proposition missing');
if (!page.includes('Fail-closed')) throw new Error('Fail-closed trust section missing');
const demoVideoTag = page.match(/<video\b[^>]*className="demoVideo"[^>]*\/>/s)?.[0];
if (!demoVideoTag) throw new Error('Local MVP demo video markup missing');
if (!/\bcontrols\b/.test(demoVideoTag)) throw new Error('MVP demo videos must expose native playback controls');
if (/\bautoPlay\b/.test(demoVideoTag) || /\bloop\b/.test(demoVideoTag)) {
  throw new Error('MVP demo videos must not autoplay or loop');
}
if (!demoVideoTag.includes('preload="metadata"')) throw new Error('MVP demo videos must remain metadata-only before user playback');

const operatorName = legalOperator.match(/^- Full legal\/operator name: `([^`]+)`/m)?.[1] || '';
if (!operatorName.startsWith('[REQUIRED')) {
  throw new Error('Public legal-operator template must not contain a factual personal name');
}
if (!legalOperator.includes('Never store identity documents, residence permits, lease agreements, tax letters, Gewerbeanmeldung certificates')) {
  throw new Error('Public legal-operator privacy boundary missing');
}
if (!legalOperator.includes('GDPR Article 13/14 completion facts') || !legalOperator.includes('AI transparency and service-boundary review')) {
  throw new Error('Legal completion template must retain GDPR and AI-transparency review gates');
}
if (!retiredPreGewerbe.includes('Status: **RETIRED**')) {
  throw new Error('Former pre-Gewerbe research workflow must remain explicitly retired');
}
if (!retiredPreGewerbe.includes('No pre-Gewerbe real-user research')) {
  throw new Error('Retired pre-Gewerbe file must prohibit real-user research');
}
const permanentStatusDocs = `${preGoStatus}\n${packageStatus}`;
if (/[0-9a-f]{40}/i.test(permanentStatusDocs)) {
  throw new Error('Permanent pre-GO status templates must not freeze historical commit SHAs');
}
if (/Frozen backend baseline/i.test(permanentStatusDocs) || /certified backend baseline/i.test(permanentStatusDocs)) {
  throw new Error('Permanent pre-GO status templates must require live backend evidence');
}
if (!preGoStatus.includes('Never treat a historical SHA, deployment, Smoke result or Certification result as current launch evidence')) {
  throw new Error('Pre-GO status must reject historical production evidence');
}
if (!packageStatus.includes('Authoritative sequencing') || !packageStatus.includes('Gewerbeanmeldung')) {
  throw new Error('Package status must retain the current owner sequencing');
}

console.log('Content preflight PASS: 6 MVP journeys, legal routes, working skip links, truthful dynamic no-store health, shared actionable CTA validation, AI transparency, trust boundaries, accessible local demos, public-operator privacy, retired pre-Gewerbe research and live-only production evidence rules present.');
