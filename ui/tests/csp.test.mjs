import assert from "node:assert/strict";
import test from "node:test";

import { apiBase, apiOrigin } from "../lib/api-base.mjs";
import { contentSecurityPolicy } from "../lib/csp.mjs";

test("client and CSP share the documented API setting", () => {
  const env = { NEXT_PUBLIC_KB_API_URL: "https://kb-api.bank.example/v1/" };
  assert.equal(apiBase(env), "https://kb-api.bank.example/v1");
  assert.equal(apiOrigin(env), "https://kb-api.bank.example");
  assert.match(contentSecurityPolicy(env, "nonce"), /connect-src 'self' https:\/\/kb-api\.bank\.example/);
});

test("a rooted reverse-proxy path stays same-origin", () => {
  const env = { NEXT_PUBLIC_KB_API_URL: "/apps/hrz2/api" };
  assert.equal(apiBase(env), "/apps/hrz2/api");
  assert.equal(apiOrigin(env), "");
  assert.match(contentSecurityPolicy(env, "nonce"), /connect-src 'self';/);
});

test("plaintext non-loopback API origins are refused", () => {
  assert.throws(
    () => apiBase({ NEXT_PUBLIC_KB_API_URL: "http://kb-api.bank.example" }),
    /must be HTTPS outside loopback/,
  );
});

test("an emptied API base chooses same-origin instead of the unset localhost default", () => {
  assert.equal(apiBase({}), "http://localhost:8082");
  assert.equal(apiBase({ NEXT_PUBLIC_KB_API_URL: "" }), "");
  const managed = contentSecurityPolicy({ NEXT_PUBLIC_KB_API_URL: "" }, "nonce");
  assert.match(managed, /connect-src 'self';/);
  assert.doesNotMatch(managed, /localhost:8082/);
});

test("an emptied frame-ancestors value refuses instead of inheriting self", () => {
  assert.match(contentSecurityPolicy({}, "nonce"), /frame-ancestors 'self'/);
  assert.throws(
    () => contentSecurityPolicy({ NEXT_PUBLIC_FRAME_ANCESTORS: "" }, "nonce"),
    /NEXT_PUBLIC_FRAME_ANCESTORS is configured but empty/,
  );
});

test("a wildcard frame-ancestors is refused rather than passed through", () => {
  // The document a browser frames is served by Next.js and never passes through the API
  // middleware, so this surface needs the refusal the API now makes. A wildcard here is the
  // clickjacking control switched off: any page on the internet may frame the console.
  //
  // SEVEN spellings, not four. The exact-token set is only half the rule, because a Set can
  // match an entry EXACTLY and nothing else. `https://*.evil.example` is in no set, so it was
  // emitted verbatim, and CSP honours that host-source form as EVERY subdomain, including one
  // an attacker obtained by takeover or one that serves user content. The token half remains
  // for `'*'` (the quoted form CSP also honours) and `null` (the origin a SANDBOXED iframe
  // presents, so allowing it hands the frame to any page that can sandbox one), which carry no
  // asterisk at all. `src/enterprise_kb/api/app.py` refuses the same union.
  const spellings = ["*", "'*'", "null", "*.*", "https://*.evil.example", "*.example", "https://*"];
  for (const spelling of spellings) {
    assert.throws(
      () => contentSecurityPolicy({ NEXT_PUBLIC_FRAME_ANCESTORS: spelling }, "nonce"),
      /wildcard/,
      `${spelling} must be refused`,
    );
    assert.throws(
      () =>
        contentSecurityPolicy(
          { NEXT_PUBLIC_FRAME_ANCESTORS: `'self' https://portal.bank.example ${spelling}` },
          "nonce",
        ),
      /wildcard/,
      `${spelling} must be refused among named origins`,
    );
  }
});

test("the states around the wildcard case are untouched", () => {
  assert.match(contentSecurityPolicy({}, "nonce"), /frame-ancestors 'self'/);
  assert.match(
    contentSecurityPolicy({ NEXT_PUBLIC_FRAME_ANCESTORS: "'none'" }, "nonce"),
    /frame-ancestors 'none'/,
  );
  assert.match(
    contentSecurityPolicy(
      { NEXT_PUBLIC_FRAME_ANCESTORS: "'self' https://portal.bank.example" },
      "nonce",
    ),
    /frame-ancestors 'self' https:\/\/portal\.bank\.example/,
  );
  assert.throws(
    () => contentSecurityPolicy({ NEXT_PUBLIC_FRAME_ANCESTORS: "" }, "nonce"),
    /NEXT_PUBLIC_FRAME_ANCESTORS is configured but empty/,
  );
});

test("the wildcard refusal leaves a legitimate named allowlist alone", () => {
  // A refusal that also turns away valid configuration is an outage, not a control. The two
  // shapes most likely to trip a careless rule are an explicit PORT and a HYPHENATED host
  // label, and `nullify` proves the token match did not quietly become a substring match.
  const named = "'self' https://portal.bank.example:8443 https://a-b-c.bank.example";
  assert.match(
    contentSecurityPolicy({ NEXT_PUBLIC_FRAME_ANCESTORS: named }, "nonce"),
    /frame-ancestors 'self' https:\/\/portal\.bank\.example:8443 https:\/\/a-b-c\.bank\.example/,
  );
  assert.match(
    contentSecurityPolicy({ NEXT_PUBLIC_FRAME_ANCESTORS: "https://nullify.example" }, "nonce"),
    /frame-ancestors https:\/\/nullify\.example/,
  );
});
