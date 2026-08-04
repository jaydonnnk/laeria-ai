/** @type {import('next').NextConfig} */

// `next dev` and `next build` both write to .next, so running a production
// build while the dev server is up replaces the chunks that server is
// handing out — every page then 404s on /_next/static/* until .next is
// deleted and dev is restarted. It looks like the app broke; nothing did.
//
// `npm run build:check` writes elsewhere, so a build can be verified at any
// time without disturbing a running dev server.
//
// npm sets npm_lifecycle_event to the script name on every platform, so this
// needs no extra dependency. Vercel runs `build`, so deploys are unaffected.
const isSideBuild = process.env.npm_lifecycle_event === "build:check";

const nextConfig = {
  reactStrictMode: true,
  distDir: isSideBuild ? ".next-check" : ".next",
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  },
};
module.exports = nextConfig;
