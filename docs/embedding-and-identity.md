# Embedding and identity: client integration guide (A2 Enterprise Knowledge Base)

This guide shows how a client runs the A2 Enterprise Knowledge Base and, when desired, embeds
its console UI inside an existing web application with secure single sign-on (SSO), so users
never see a second login. It is grounded in what the codebase implements today. The single
invariant across every shape: the server never trusts a client-asserted `actor` or ACL. The
audit actor and the entitlement principals that scope ACL-aware retrieval both come from a
server-verified `Principal`, resolved by the `IdentityPort` adapter for the active profile.

The KB ships as two cooperating pieces:

- Backend: a FastAPI service (default port `8082`) exposing the governed-RAG surface
  (`/v1/search`, `/v1/answer`, local-only `/v1/ingest`, local-only `DELETE /v1/documents/{id}`,
  `/v1/corpus/status`), plus `/healthz`, the persona list (`/v1/personas`), and the
  governed-RAG peer manifest (`/.well-known/governed-rag-contract`). Agent Runtime/A2A
  discovery remains disabled until a trusted invocation-context bridge is implemented.
- UI: a Next.js console (default port `3000`) that calls the backend and renders the cited,
  ACL-filtered passages and grounded answers. `NEXT_PUBLIC_EMBED=1` drops the UI's own chrome;
  the UI base path and API base are build-time env vars.

---

## 1. Three deployment shapes

Pick the cheapest shape the host can actually satisfy.

| # | Shape | Use when the host | Host work | Identity |
|---|-------|-------------------|-----------|----------|
| 1 | Embedded same-origin (reverse-proxy iframe) | controls its own edge (nginx / Next.js rewrites) and can federate its IdP into Cloud IAP (Workforce Identity Federation). | Two proxy routes (`/kb/*`, `/kb/api/*`) plus one `<iframe src="/kb/">`. | IAP-verified `x-goog-iap-jwt-assertion`; the proxy forwards the header. |
| 2 | Standalone behind Cloud IAP | has no host app, or wants a separate console at its own URL. | DNS plus HTTPS load balancer plus IAP. | IAP-verified assertion; IAP with WIF gives SSO. |
| 3 | Local dev (no auth) | is evaluating offline, with no IdP. | None. | Seeded dev personas via the `X-Dev-Persona` header. |

Host-fit summary: controls-edge and GCP-aligned goes to shape 1; no host app goes to shape 2;
offline evaluation goes to shape 3.

---

## 2. Shape 3 (local): run locally, no auth

Local mode (`KB_PROFILE=local`, the default) runs the entire pipeline offline: SQLite FTS5
retrieval, a deterministic LLM, and no IdP, AD, or LDAP. Identity is resolved from a small set
of seeded dev personas (`src/enterprise_kb/adapters/local/identity.py`) selected by an
`X-Dev-Persona` request header, with the first persona as the default.

```bash
# Backend (repo root)
export KB_PROFILE=local
make run-api                      # uvicorn on http://localhost:8082

# Non-loopback is refused unless the operator explicitly accepts local no-IdP exposure:
KB_ALLOW_INSECURE_DEMO=1 make run-api API_HOST=0.0.0.0

# UI (in ./ui)
cp .env.local.example .env.local  # NEXT_PUBLIC_KB_API_URL defaults to http://localhost:8082
npm install && npm run dev        # http://localhost:3000
```

The UI fetches `GET /v1/personas` and sends the chosen id as `X-Dev-Persona`. The seeded
personas deliberately span different entitlements and tenants (including a cross-tenant one),
so per-user, ACL-aware access is demoable offline. Each persona's principals are group ids the
local access-control directory resolves to ACL tags, so switching persona changes which corpus
passages the same query admits:

| Persona id | Subject | Tenant | Entitlement principals | Sees in the seed corpus |
|-----------|---------|--------|------------------------|-------------------------|
| `analyst` | `demo.analyst@bank.example` | `demo-bank` | `group:kb-reader`, `group:risk` | cloud-onboarding, incident-runbook, global notice |
| `approver` | `demo.approver@bank.example` | `demo-bank` | `group:kb-reader`, `group:risk`, `group:kb-approver` | all four (incl. restricted data-residency) |
| `auditor` | `demo.auditor@bank.example` | `demo-bank` | `group:audit` | the global notice only (internal-only, subset match) |
| `other-tenant` | `user@other-tenant.example` | `other-bank` | `group:kb-reader` | the global notice only (tenant-isolated) |

Matching is **all-of / subset** (a caller must hold every one of a passage's tags), so the
`auditor` holding only `classification:internal` sees just the internal-only global notice,
not every doc that merely carries that tag. The **tenant** partition isolates `other-tenant`
to shared/global (`""`-tenant) content even though it holds the same `group:kb-reader` as a
demo-bank reader.

```bash
curl -s http://localhost:8082/v1/personas | python -m json.tool

# No "actor" in the body: identity comes from the verified persona.
curl -s -X POST http://localhost:8082/v1/answer \
  -H 'Content-Type: application/json' -H 'X-Dev-Persona: approver' \
  -d '{"query": "Where must records classified restricted be stored?", "acl_principals": []}' \
  | python -m json.tool
```

In secure profiles `X-Dev-Persona` is ignored entirely (Section 4), so leaving persona-selection
code in the UI is harmless in production. `GET /v1/personas` returns an empty list outside
`local`, and the UI shows the picker only when `GET /healthz` reports `profile == "local"`.

---

## 3. Shape 1 (embedded): same-origin reverse proxy

This is the smallest change for a host that controls its edge: serve the KB under your own
origin at a sub-path (for example `/kb/`) via a reverse proxy, then drop an iframe pointing at
that same-origin path. Because the iframe is first-party, there are no third-party-cookie issues
and no CORS to configure. The client owns exactly two things: a proxy route and an iframe tag.

### 3a. Reverse-proxy the KB service

nginx:

```nginx
# On https://portal.client.com
location /kb/ {
    proxy_pass         http://kb-ui.internal:3000/;      # the Next.js UI
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-Proto $scheme;
}

# The UI's API calls (NEXT_PUBLIC_KB_API_URL=/kb/api) also resolve same-origin:
location /kb/api/ {
    proxy_pass         http://kb-backend.internal:8082/;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-Proto $scheme;
    # IAP runs in front of this origin, so the x-goog-iap-jwt-assertion header is present
    # on the inbound request and is forwarded through to the backend.
}
```

Next.js host app (if the parent is itself Next.js, use `rewrites()` in its own config):

```js
// next.config.mjs of the PARENT app
const nextConfig = {
  async rewrites() {
    return [
      { source: "/kb/api/:path*", destination: "http://kb-backend.internal:8082/:path*" },
      { source: "/kb/:path*",     destination: "http://kb-ui.internal:3000/:path*" },
    ];
  },
};
export default nextConfig;
```

### 3b. Mount the UI under the sub-path and hide its chrome

```bash
# Environment for the KB UI (build-time)
NEXT_PUBLIC_BASE_PATH=/kb          # mount the UI (and its assets) under the sub-path
NEXT_PUBLIC_KB_API_URL=/kb/api     # same-origin API calls (no CORS needed)
NEXT_PUBLIC_EMBED=1                # hide the UI's own header/nav chrome when embedded
```

### 3c. The iframe tag (host page)

```html
<!-- On https://portal.client.com, inside your existing page (a sized container) -->
<iframe
  src="/kb/"
  title="Enterprise Knowledge Base"
  style="width:100%; height:100%; border:0;"
  loading="lazy">
</iframe>
```

Height caveat: `height:100%` renders correctly only inside a host container that already has a
fixed pixel height. There is no child-to-parent resize message today, so give the iframe a sized
container.

### 3d. Allow the parent origin to frame the UI

The backend emits `Content-Security-Policy: frame-ancestors <KB_FRAME_ANCESTORS>` via middleware
(`src/enterprise_kb/api/app.py`), and adds `X-Frame-Options: SAMEORIGIN` only when the value is
`'self'` (the legacy header cannot express a multi-origin allowlist, so the CSP directive owns
the multi-origin case):

```bash
export KB_FRAME_ANCESTORS="https://portal.client.com"
# multiple parents are space-separated, per the CSP grammar:
# export KB_FRAME_ANCESTORS="https://portal.client.com https://admin.client.com"
```

`KB_FRAME_ANCESTORS` resolves in **three** states, because unset is not one of its valid
values:

| State | Result |
| --- | --- |
| unset | the shipped default `'self'` |
| set, naming no origin (`""` or whitespace) | the service REFUSES to start |
| set to one or more origins | exactly those origins |

Reading the middle state as unset would emit the header
`Content-Security-Policy: frame-ancestors ` with an empty directive; browsers discard an empty
directive as a parse error, and the `'self'` branch that adds `X-Frame-Options` would be skipped
too, so the clickjacking restriction would disappear from both channels at once with nothing in
the response to say so. A config template that renders the variable empty fails at boot
instead.
To forbid all framing, say so explicitly:

```bash
export KB_FRAME_ANCESTORS="'none'"
```

Scope note: `frame-ancestors` is honored only on the HTTP response of the document the browser
actually frames, and only as a real response header (not a `<meta>` element). In shape 1 the
framed document is served same-origin through the proxy, so the backend header reaches it.

---

## 4. Shape 2 (standalone): behind Cloud IAP

When there is no host application, deploy the KB on its own URL:

1. Deploy backend and UI behind the same HTTPS load balancer and Cloud IAP.
2. Set `KB_PROFILE=gcp` and `KB_IAP_AUDIENCE` so the backend verifies the IAP assertion.
3. Point the UI at the backend with `NEXT_PUBLIC_KB_API_URL`. If UI and backend are on different
   origins, also set `KB_CORS_ORIGINS` to the UI origin (explicit allowlist, never `"*"`):

   ```bash
   export KB_CORS_ORIGINS="https://kb.client.com"
   export NEXT_PUBLIC_KB_API_URL="https://api.kb.client.com"
   ```

4. Share the URL with authorized users. IAP with Workforce Identity Federation gives silent SSO
   from the corporate IdP while the corporate session is live.

Leave `KB_FRAME_ANCESTORS` at its `'self'` default: nothing should iframe a standalone deployment.

---

## 5. The identity contract

The single invariant, implemented today and preserved across every shape: the server never
trusts a client-asserted actor or ACL.

- `get_principal` (`src/enterprise_kb/api/security.py`) builds a `RequestContext` from the
  inbound request headers only, asks the active `IdentityPort` adapter to resolve a verified
  `Principal`, and turns any `IdentityError` into a hard 401.
- Every enabled data route (`/v1/search`, `/v1/answer`, and the local-demo ingest/delete routes,
  `/v1/corpus/status`) depends on `CurrentPrincipal`. The request schemas carry no `actor`
  field: the audit actor is `principal.actor` (the verified subject).
- The ACL-aware retrieval scope is the verified principal's own entitlement, run through an
  entitlement check: `acl_principals = principal.entitlement_principals(request.acl_principals)`.
  A client-supplied `acl_principals` is an entitlement-checked scope-DOWN hint : it may only
  narrow to a subset of the ids the principal already holds, and any id it does not hold is
  dropped, so a caller can never widen its own visibility by asserting a privileged group id.
  The `tenant` fed to retrieval is likewise `principal.tenant`, never client-supplied, and the
  domain enforces the tenant partition and all-of/subset tag matching, dropping untagged
  passages fail-closed (P-09).

The `Principal` (`src/enterprise_kb/domain/identity.py`) models everything enforcement needs:
`subject` (the audit actor), `principals` (entitlement groups fed into governed retrieval),
`tenant` (multi-tenant partition), `assurance` (auth-strength hint), and `source` (which adapter
resolved it).

Identity adapters by profile (`config/settings.yaml`, `adapters.identity`):

| Profile | Adapter | Behaviour |
|---------|---------|-----------|
| `local` | `LocalPersonaIdentityAdapter` | Seeded dev personas via `X-Dev-Persona`, no IdP. Default is the first persona; unknown id is a 401. |
| `gcp` / `platform` | `IapIdentityAdapter` | Verifies the signed `x-goog-iap-jwt-assertion` (signature, audience `KB_IAP_AUDIENCE`, issuer, expiry) against Google's IAP keys. The immutable `sub` is the audit subject. Human tenant comes from `hd`, then the verified email domain, then `sub`; service-account tenants require an exact reviewed email mapping. The verified email/sub also becomes the directory principal. Google imports are lazy so SDK-free profiles stay clean; the assertion is never logged. |
| `onprem` | `OnPremIdentityAdapter` | Fail-fast placeholder for the client's own enterprise IdP (OIDC/SAML): raises rather than accept an unverified caller. |

Defense-in-depth PEP: edge (Cloud IAP / Apigee) authenticates at ingress, a central guardrail
applies policy, and this backend re-verifies the assertion and derives identity itself, then
enforces per-user ACLs in retrieval. Each layer assumes the others may be bypassed. This is the
seam that defeats actor spoofing and the confused-deputy risk.

---

## 6. Configuration knobs

| Variable | Side | Purpose |
|----------|------|---------|
| `KB_PROFILE` | backend | `local` \| `gcp` \| `platform` \| `onprem`. Selects the identity adapter (and the whole adapter set). |
| `KB_IAP_AUDIENCE` | backend | The IAP audience string (the exact structured protected-resource path) the backend verifies against. Required in `gcp`/`platform`. |
| `KB_CORS_ORIGINS` | backend | Explicit origin allowlist for the cross-origin / standalone case. Comma-separated. Never `"*"`; defaults to the local dev origins. |
| `KB_FRAME_ANCESTORS` | backend | CSP `frame-ancestors` allowlist: parent origins permitted to iframe the UI. Space-separated. Defaults to `'self'` when unset; set-and-empty refuses to boot; say `'none'` to forbid all framing. |
| `NEXT_PUBLIC_KB_API_URL` | UI | Backend base URL the UI calls. Build-time. |
| `NEXT_PUBLIC_BASE_PATH` | UI | Sub-path the UI (and assets) are mounted under. Blank keeps the standalone build. Build-time. |
| `NEXT_PUBLIC_EMBED` | UI | Set to `1` to hide the UI's own chrome when embedded. Build-time. |
| `X-Dev-Persona` | request header | Local profile only. Selects a seeded dev persona; ignored in secure profiles. |

---

## 7. Checklists

### Client-side integration checklist

Shape 1 (same-origin reverse proxy):

- [ ] Reverse-proxy route mapping `/kb/*` to the KB UI service (3a).
- [ ] Reverse-proxy route mapping `/kb/api/*` to the KB backend service.
- [ ] `<iframe src="/kb/">` on the host page, inside a sized container (3c).
- [ ] `KB_FRAME_ANCESTORS` set to the exact parent origin(s) (3d).
- [ ] IdP federated into IAP (WIF) so users carry one session through.

Shape 2 (standalone):

- [ ] DNS plus HTTPS load balancer plus IAP fronting the deployment.
- [ ] `KB_PROFILE=gcp` and `KB_IAP_AUDIENCE` set so the backend verifies the assertion.
- [ ] `KB_CORS_ORIGINS` set if the UI and backend are on different origins.
- [ ] URL shared with authorized users/groups.

### Security checklist

- [ ] HTTPS everywhere (the load balancer terminates TLS; IAP requires it).
- [ ] IAP audience configured (`KB_IAP_AUDIENCE`) in any IAP profile; the backend refuses to
      verify without it.
- [ ] Framing locked down: `KB_FRAME_ANCESTORS` set to exact parent origin(s); `'self'` for
      standalone; never a wildcard.
- [ ] Origins locked down: same-origin proxy (no CORS) for shape 1; otherwise `KB_CORS_ORIGINS`
      is an explicit allowlist, never `"*"`.
- [ ] No client-asserted identity trusted: production uses `gcp`/`platform` (or an implemented
      `onprem`), never `local`. No request carries an `actor`.

---

## 8. Further layers (not built in this slice)

This slice covers same-origin embedding, standalone-behind-IAP, and local dev, all re-verified
server-side. The following deepen the posture and can be added on the same `IdentityPort` seam
without a domain change. They mirror the reference build in `cdd-sow-research`, whose
`docs/embedding-and-identity.md` specifies them in full:

- Cross-origin token-handoff embedding (a versioned loader plus a framework-agnostic web
  component, a hardened `postMessage` contract, and a bearer-token-in-memory handoff) for hosts
  that can run neither a reverse proxy nor IAP federation.
- A host-IdP bearer adapter that verifies `Authorization: Bearer <jwt>` against a per-tenant
  trusted-issuer / JWKS / audience allowlist (RS256/ES256 pinned), plus RFC 8693 token exchange
  so a leaked token cannot be replayed against the host's other APIs.
- A "launch in new tab" standalone OIDC redirect login (Authorization Code with PKCE) for hosts
  that accept top-level navigation.
- Per-hop OAuth2 token exchange (OBO) with Workload Identity and mTLS to the sibling Hrz
  services; step-up / assurance checks (acr/amr) before consequential actions.
- Per-tenant framing/CORS/issuer registry and a full XSS-containment CSP (with Trusted Types) on
  the framed UI document. (KB tenant partitioning and fail-closed all-of/subset ACLs are now
  enforced in the domain : the remaining item here is the per-tenant *edge* framing/issuer
  registry, not the corpus-level isolation.)
