import fs from 'node:fs';
const page = fs.readFileSync(new URL('../app/page.tsx', import.meta.url), 'utf8');
const layout = fs.readFileSync(new URL('../app/layout.tsx', import.meta.url), 'utf8');
const legal = fs.readFileSync(new URL('../app/[legal]/page.jsx', import.meta.url), 'utf8');
const beta = fs.readFileSync(new URL('../app/beta/page.tsx', import.meta.url), 'utf8');
const health = fs.readFileSync(new URL('../app/api/health/route.js', import.meta.url), 'utf8');
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
if (!page.includes('KI-gestützter')) throw new Error('AI transparency copy missing');
if (!page.includes('5 Sprachen')) throw new Error('Language value proposition missing');
if (!page.includes('Fail-closed')) throw new Error('Fail-closed trust section missing');
if (!page.includes('NEXT_PUBLIC_BETA_CTA_URL')) throw new Error('Beta CTA target environment boundary missing');
if (!page.includes('href={betaCtaUrl}')) throw new Error('Enabled Beta CTA must be an actionable link');
if (!page.includes('url.hostname === "wa.me"') || !page.includes('url.hostname === "api.whatsapp.com"')) {
  throw new Error('Beta CTA must restrict targets to approved WhatsApp hosts');
}
if (!page.includes('<button className="cta" disabled>')) throw new Error('Disabled pre-GO CTA boundary missing');
const demoVideoTag = page.match(/<video\b[^>]*className="demoVideo"[^>]*\/>/s)?.[0];
if (!demoVideoTag) throw new Error('Local MVP demo video markup missing');
if (!/\bcontrols\b/.test(demoVideoTag)) throw new Error('MVP demo videos must expose native playback controls');
if (/\bautoPlay\b/.test(demoVideoTag) || /\bloop\b/.test(demoVideoTag)) {
  throw new Error('MVP demo videos must not autoplay or loop');
}
if (!demoVideoTag.includes('preload="metadata"')) throw new Error('MVP demo videos must remain metadata-only before user playback');
console.log('Content preflight PASS: 6 MVP journeys, legal routes, working skip links, dynamic no-store health, AI transparency, trust boundaries, accessible local demos and fail-closed actionable CTA present.');
