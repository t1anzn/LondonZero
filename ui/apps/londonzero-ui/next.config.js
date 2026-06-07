/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Allow the dev server to serve /_next/* assets when the UI is reached over
  // Tailscale / the LAN (not just localhost). Covers scan-13's Tailscale IP,
  // the .local hostname, and the private LAN ranges.
  allowedDevOrigins: [
    "100.104.211.67",
    "scan-13.local",
    "scan-13",
    "10.18.216.48",
  ],
  env: {
    // NEXT_PUBLIC_API_URL is resolved at runtime in lib/api.ts from the page's
    // own hostname, so the app is portable across Tailscale / LAN / localhost.
    NEXT_PUBLIC_DEFAULT_LAT: "51.5133",
    NEXT_PUBLIC_DEFAULT_LON: "-0.0886",
  },
};

module.exports = nextConfig;
