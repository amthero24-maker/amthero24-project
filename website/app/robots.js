export default function robots() {
  const indexable = process.env.NEXT_PUBLIC_SITE_INDEXABLE === "true";
  return indexable
    ? { rules: { userAgent: "*", allow: "/" }, sitemap: "https://amthero24.de/sitemap.xml" }
    : { rules: { userAgent: "*", disallow: "/" } };
}
