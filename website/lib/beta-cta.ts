type BetaCtaEnvironment = {
  NEXT_PUBLIC_BETA_CTA_ENABLED?: string;
  NEXT_PUBLIC_BETA_CTA_URL?: string;
};

export function resolveBetaCtaUrl(
  environment: BetaCtaEnvironment = process.env,
): string | null {
  if (environment.NEXT_PUBLIC_BETA_CTA_ENABLED !== "true") return null;
  const raw = environment.NEXT_PUBLIC_BETA_CTA_URL?.trim();
  if (!raw) return null;

  try {
    const url = new URL(raw);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.port ||
      url.hash
    ) {
      return null;
    }

    if (url.hostname === "wa.me") {
      const path = url.pathname.replace(/\/+$/, "");
      if (!/^\/(?:\d{6,20}|message\/[A-Za-z0-9_-]{5,64})$/.test(path)) {
        return null;
      }
    } else if (url.hostname === "api.whatsapp.com") {
      const phone = url.searchParams.get("phone") || "";
      if (url.pathname !== "/send" || !/^\d{6,20}$/.test(phone)) {
        return null;
      }
    } else {
      return null;
    }

    return url.toString();
  } catch {
    return null;
  }
}
