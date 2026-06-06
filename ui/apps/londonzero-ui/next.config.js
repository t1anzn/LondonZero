/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    // NEXT_PUBLIC_API_URL intentionally not set here — undefined in dev triggers mock route
    // Set it in .env.local to point at the real FastAPI backend
    NEXT_PUBLIC_DEFAULT_LAT: "51.5133",
    NEXT_PUBLIC_DEFAULT_LON: "-0.0886",
  },
};

module.exports = nextConfig;
