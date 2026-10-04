import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * 1. Access logging (visible in the next-server process output) so user journeys
 *    can be traced when the in-app preview misbehaves.
 * 2. Diagnostic: for /api/auth/* requests, log which safe auth carriers
 *    survived the preview proxy (headers / HttpOnly cookie).
 * 3. Token bridging: if the Authorization header is missing, restore it from
 *    the server-issued gs_access cookie. Tokens are never carried in URLs.
 * 4. Locale stays cookie-driven (NEXT_LOCALE) - no URL prefixing, no i18n middleware.
 */
export function middleware(req: NextRequest) {
  console.log(`[req] ${req.method} ${req.nextUrl.pathname}`);

  if (req.nextUrl.pathname.startsWith("/api/")) {
    const header = req.headers.get("authorization");
    const cookieTok = req.cookies.get("gs_access")?.value;
    if (req.nextUrl.pathname.startsWith("/api/auth/")) {
      const names: string[] = [];
      req.headers.forEach((_, name) => names.push(name));
      const cookieNames = Object.keys(req.cookies.getAll()).join(",");
      console.log(
        `[diag] ${req.method} ${req.nextUrl.pathname} | headers=[${names}] | ` +
          `authorization=${header ? "yes" : "NO"} | ` +
          `cookies=[${cookieNames || "none"}]`
      );
    }

    const fallback = cookieTok;
    if (!header && fallback) {
      const requestHeaders = new Headers(req.headers);
      requestHeaders.set("authorization", `Bearer ${fallback}`);
      return NextResponse.next({ request: { headers: requestHeaders } });
    }
  }
  return NextResponse.next();
}

export const config = {
  // skip static assets
  matcher: ["/((?!_next|favicon.ico|.*\\..*).*)"],
};
