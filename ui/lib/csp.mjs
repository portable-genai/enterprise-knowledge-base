// The console's Content-Security-Policy, in one module so it is built once and read twice.
//
// Emitting it inline in `next.config.mjs` through the static `headers()` table is what this
// module avoids. That
// table cannot express a per-request value, which is exactly what a script nonce is, and the
// policy it emitted carried `script-src 'self'` under a comment asserting "the console ships no
// inline scripts". That assertion was false: Next serves its hydration bootstrap as an INLINE
// script carrying the Flight payload, so the browser blocked it, `__next_f` never filled and
// React never attached. The console rendered its controls and none of them did anything,
// while the headers, the type-check, the build and every test stayed green.
//
// So the policy moved here, the nonce is minted per request in `proxy.ts`, and `next.config.mjs`
// no longer emits a `Content-Security-Policy` at all. Two layers both setting it would give the
// browser two policies to intersect, and the stricter one wins, which would quietly reinstate the
// defect this module exists to remove.

import { apiOrigin } from "./api-base.mjs";
import { settingOrDefault } from "./env-setting.mjs";

/**
 * Values that must never be accepted as a framing ancestor.
 *
 * Four spellings, not one: `'*'` is the quoted form CSP also honours, `*.*` is the subdomain
 * wildcard, and `null` is the origin a SANDBOXED iframe presents, so allowing it hands the frame
 * to any page that can sandbox one. The API refuses the same set in
 * `src/enterprise_kb/api/app.py`; this module is the surface that decides for the DOCUMENT a
 * browser actually frames, so closing only the API would leave the more exploitable half open.
 *
 * The set alone was never the whole rule, because a Set can only match an entry EXACTLY. A
 * host-source form such as `https://*.evil.example` is in no set and was emitted verbatim, and
 * CSP honours it as EVERY subdomain, including one an attacker obtained by takeover or one that
 * serves user content. `isWildcard` is therefore the union of the two halves.
 */
const FRAME_ANCESTOR_WILDCARDS = new Set(["*", "'*'", "null", "*.*"]);

/**
 * An entry is a wildcard when it carries an asterisk ANYWHERE, or when it is one of the exact
 * tokens above. A legitimate origin holds no asterisk, so the first half refuses nothing a
 * deployment could correctly hold; the second half exists for the tokens that carry none.
 * Matching for those is exact, so `https://nullify.example` stays a perfectly good origin.
 *
 * @param {string} part
 * @returns {boolean}
 */
function isWildcard(part) {
  return FRAME_ANCESTOR_WILDCARDS.has(part) || part.includes("*");
}

/** Who may frame this console. Unset means the same origin and nobody else. */
export function frameAncestors(env) {
  const value = settingOrDefault(env, "NEXT_PUBLIC_FRAME_ANCESTORS", "'self'");
  for (const part of value.split(/\s+/).filter(Boolean)) {
    if (isWildcard(part)) {
      throw new Error(
        `NEXT_PUBLIC_FRAME_ANCESTORS contains ${JSON.stringify(part)}: the origin policy must ` +
          "never contain a wildcard. That is the clickjacking control switched off, since any " +
          "site could then frame this console. Name the exact parent origins instead, unset the " +
          "variable to keep the 'self' default, or set it to 'none' to refuse all framing.",
      );
    }
  }
  return value;
}

/**
 * The full default-deny policy.
 *
 * `style-src` carries `'unsafe-inline'` because the Next runtime injects critical CSS and there
 * is no nonce path for it. `script-src` does NOT: it takes the per-request nonce plus
 * `'strict-dynamic'`, so the nonced bootstrap may load its own chunks and nothing else may run.
 * Passing no nonce yields the strict `'self'` form, which is correct for any response that is not
 * a Next-rendered document and wrong for one that is.
 *
 * @param {Record<string, string | undefined>} env
 * @param {string} [nonce]
 */
export function contentSecurityPolicy(env, nonce) {
  const connectSrc = ["'self'", apiOrigin(env)].filter(Boolean).join(" ");
  const scriptSrc = nonce
    ? `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`
    : "script-src 'self'";
  return [
    "default-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    scriptSrc,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src ${connectSrc}`,
    `frame-ancestors ${frameAncestors(env)}`,
  ].join("; ");
}

/** A fresh per-request nonce. Base64 of 16 random bytes from the Web Crypto global. */
export function generateNonce() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

/** Raised when the nonce policy and the rendering mode disagree, which serves un-hydratable HTML. */
export class UnhydratableCspError extends Error {}

/**
 * Refuse a build whose CSP mints a nonce the rendered HTML can never carry.
 *
 * Next can only stamp a per-request nonce onto the scripts of a DYNAMICALLY rendered route. A
 * statically prerendered page was built before the nonce existed, so it emits bare script tags
 * while the header advertises a nonce, and because `'strict-dynamic'` switches off the `'self'`
 * fallback, that combination blocks strictly MORE than the unfixed policy did. The failure is
 * invisible to every check that does not execute the page, so it is refused at build time.
 *
 * @param {string} layoutSource contents of `app/layout.tsx`
 */
export function assertHydratableCsp(layoutSource) {
  if (!/export\s+const\s+dynamic\s*=\s*["']force-dynamic["']/.test(layoutSource)) {
    throw new UnhydratableCspError(
      'app/layout.tsx must set `export const dynamic = "force-dynamic"`. The CSP mints a ' +
        "per-request nonce, and Next can only stamp it onto script tags for a dynamically " +
        "rendered route. Statically prerendered HTML was built before the nonce existed, so " +
        "every script is blocked and the page never hydrates.",
    );
  }
}
