const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0",
  Pragma: "no-cache",
  Expires: "0",
};

export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json(
    {
      status: "ok",
      service: "amthero24-website",
      indexable: process.env.NEXT_PUBLIC_SITE_INDEXABLE === "true",
      betaCta: process.env.NEXT_PUBLIC_BETA_CTA_ENABLED === "true",
    },
    { status: 200, headers: NO_STORE_HEADERS },
  );
}
