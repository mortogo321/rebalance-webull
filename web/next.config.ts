import type { NextConfig } from "next";

const config: NextConfig = {
  // A self-contained server bundle with only the files it actually imports.
  // The runtime image is then a slim base plus this directory, rather than a
  // full node_modules tree -- smaller images pull faster, which is time the
  // blue/green swap spends waiting.
  output: "standalone",

  reactStrictMode: true,

  // Development only, and required because the dev server runs in a container.
  //
  // Next 16 blocks cross-origin requests to dev-only resources (`/_next/hmr`,
  // `/__nextjs_font/*`). Inside the container the server does not recognise the
  // host's `127.0.0.1:3000` as its own origin, so it blocked them — the HMR
  // websocket handshake failed, the Turbopack client never finished booting,
  // and the page rendered from the server but never hydrated. Every client
  // component then sat at its initial state: the Portfolio panel stuck on
  // "Loading…", "Split evenly" doing nothing, no form able to submit.
  //
  // Both spellings are listed because the redirect URLs allow both.
  allowedDevOrigins: ["127.0.0.1", "localhost"],

  // Fail the production build on a type error rather than shipping it. The
  // default is already false; stating it makes explicit that CI's typecheck job
  // is a fast signal, not the only gate.
  // (Next.js 16 removed the `eslint` key from NextConfig -- linting is a
  // separate CI step now, see .github/workflows/ci.yml.)
  typescript: { ignoreBuildErrors: false },

  // Surfaced on the dashboard so a deployed environment can be identified
  // without shell access -- which matters when confirming a rollback landed.
  env: {
    NEXT_PUBLIC_GIT_SHA: process.env.GIT_SHA ?? "dev",
    NEXT_PUBLIC_APP_ENV: process.env.APP_ENV ?? "development",
  },

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default config;
