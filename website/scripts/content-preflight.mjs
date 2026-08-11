import fs from 'node:fs';
const page = fs.readFileSync(new URL('../app/page.tsx', import.meta.url), 'utf8');
const legal = fs.readFileSync(new URL('../app/[legal]/page.jsx', import.meta.url), 'utf8');
const tools = ['Brief Scanner','Termin Assistance','Kündigung','Vertrags-Check','Geld zurück','Nachrichten & E-Mails'];
const routes = ['impressum','datenschutz','agb','widerruf','kontakt','beta','cookie-einstellungen','barrierefreiheit'];
for (const item of tools) if (!page.includes(item)) throw new Error(`Missing MVP tool: ${item}`);
for (const route of routes) if (!legal.includes(route)) throw new Error(`Missing legal route: ${route}`);
if (!page.includes('KI-gestützter')) throw new Error('AI transparency copy missing');
if (!page.includes('5 Sprachen')) throw new Error('Language value proposition missing');
if (!page.includes('Fail-closed')) throw new Error('Fail-closed trust section missing');
if (!page.includes('NEXT_PUBLIC_BETA_CTA_URL')) throw new Error('Beta CTA target environment boundary missing');
if (!page.includes('href={betaCtaUrl}')) throw new Error('Enabled Beta CTA must be an actionable link');
if (!page.includes('url.hostname === "wa.me"') || !page.includes('url.hostname === "api.whatsapp.com"')) {
  throw new Error('Beta CTA must restrict targets to approved WhatsApp hosts');
}
if (!page.includes('<button className="cta" disabled>')) throw new Error('Disabled pre-GO CTA boundary missing');
console.log('Content preflight PASS: 6 MVP journeys, legal routes, AI transparency, trust boundaries and fail-closed actionable CTA present.');
