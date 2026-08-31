/** @type {import('next').NextConfig} */
// The Content-Security-Policy is NOT set here. It carries a per-request script nonce, which a
// static `headers()` table cannot express, so `proxy.ts` owns it and builds it from
// `lib/csp.mjs`. Setting the policy in both places would hand the browser two policies to
// intersect, and the stricter one wins, which would reinstate the bare `script-src 'self'` that
// stopped this console hydrating in the first place.
//
// What IS here is the refusal: `next build` and `next start` both evaluate this file at module
// scope, so a layout that has lost its `force-dynamic` (and therefore cannot carry the nonce)
// fails the build instead of shipping a console whose controls silently do nothing.
import { readFileSync } from "node:fs";

import { assertHydratableCsp } from "./lib/csp.mjs";
import { readEnvSetting } from "./lib/env-setting.mjs";

assertHydratableCsp(readFileSync(new URL("./app/layout.tsx", import.meta.url), "utf8"));
// NEXT_PUBLIC_BASE_PATH mounts the UI (and its assets) under a reverse-proxy sub-path
// (for example /kb) so it can be embedded same-origin; blank keeps the standalone build
// unchanged.
const basePath = readEnvSetting(process.env, "NEXT_PUBLIC_BASE_PATH").value;

const nextConfig = {
  reactStrictMode: true,
  // `next dev` writes AGENTS.md and CLAUDE.md into this directory unless this is false; the
  // writer is node_modules/next/dist/server/lib/generate-agent-files.js. This repo's working
  // agreement is the AGENTS.md at its root and there is no tool-specific alias of it, so a
  // second one here is a second agreement to keep in step and CLAUDE.md is precisely the alias
  // the convention forbids. The generated prose also carries an em-dash, which the catalog's
  // house style forbids in shipped markdown. tests/unit/test_ui_agent_documents.py fails the
  // gate if this line goes away or if either file turns up on disk anyway.
  agentRules: false,
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          // X-Frame-Options moved to `proxy.ts` alongside the frame-ancestors it backstops, so
          // the two cannot be edited apart and disagree.
        ],
      },
    ];
  },
};

export default nextConfig;
