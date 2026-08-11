export async function GET() {
  return Response.json({ status: "ok", service: "amthero24-website", indexable: process.env.NEXT_PUBLIC_SITE_INDEXABLE === "true", betaCta: process.env.NEXT_PUBLIC_BETA_CTA_ENABLED === "true" }, { status: 200 });
}
