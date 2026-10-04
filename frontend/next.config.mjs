import createNextIntlPlugin from "next-intl/plugin";

/** @type {import('next').NextConfig} */
const API_URL = process.env.API_URL || "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [
      {
        // Never serve stale app documents from any cache (hashed /_next
        // assets stay cacheable - they are content-addressed).
        source: "/((?!_next).*)",
        headers: [{ key: "Cache-Control", value: "no-store, no-cache, must-revalidate" }],
      },
    ];
  },
  async rewrites() {
    // All browser API calls use the /api prefix; Next proxies to FastAPI.
    // Page routes (/bidder, /admin/... etc.) never clash with API paths.
    return [
      {
        source: "/api/:path*",
        destination: `${API_URL}/:path*`,
      },
    ];
  },
};

const withNextIntl = createNextIntlPlugin("./i18n.ts");

export default withNextIntl(nextConfig);
