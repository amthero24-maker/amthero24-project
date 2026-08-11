export default function sitemap() {
  if (process.env.NEXT_PUBLIC_SITE_INDEXABLE !== "true") return [];
  const routes = ["", "/impressum", "/datenschutz", "/agb", "/widerruf", "/kontakt", "/beta", "/cookie-einstellungen", "/barrierefreiheit"];
  return routes.map((route) => ({ url: `https://amthero24.de${route}`, lastModified: new Date("2026-08-11T00:00:00+02:00") }));
}
