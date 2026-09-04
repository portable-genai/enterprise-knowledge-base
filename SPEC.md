# `enterprise-knowledge-base` : Build Specification

> **Authority:** SPEC > ARCHITECTURE > COMPLIANCE > README > `docs/`. See [`docs/doc-authority.md`](docs/doc-authority.md).

## 1. What `enterprise-knowledge-base` is

`enterprise-knowledge-base` is the shared, **ACL-aware governed RAG** over the bank corpus: the "brain" every other
agent queries. It ingests documents (with ACL tags, residency and freshness metadata),
extracts them with a portable parser, redacts PII before persistence, and
serves **ACL-filtered, cited** passages and (optionally) a grounded synthesized answer. It
is a horizontal platform service, not a vertical app.

Catalog identity: `enterprise-knowledge-base`, group `hrz` (Horizon control plane), priority **P0**, buyer
Data / Knowledge Engineering. Service port default **8082** (`KNOWLEDGE_BASE_URL`).

## 2. Locked decisions

- **Region** pinned to `asia-southeast1` (Singapore). Single-region governed corpus (P-03).
  No global / multi-region store.
- **Retrieval**: AlloyDB PostgreSQL full-text search. SQL pushes down verified tenant,
  resolved all-of ACL tags and not-expired freshness before ranking/limit; the domain repeats
  the ACL decision (P-09). This contract ports to PostgreSQL outside GCP.
- **Ingest**: portable PDF/text extraction happens in memory, DLP and guardrails run on the
  extracted text, and only redacted UTF-8 reaches single-region CMEK GCS and AlloyDB chunks.
- **Freshness + residency** recorded in an AlloyDB ledger; documents past their TTL are
  re-indexed out of band by the refresh job.
- **Profiles**: `gcp` (managed), `local` (a WORKING offline laptop stack), `platform`
  (remote clients to `agent-guardrail-gateway`, `agent-registry`, `agent-observability`), `onprem` (fail-fast SDK-free placeholders). Selected by
  `KB_PROFILE`, which every deployment sets explicitly (`gcp` in production, `local` for dev,
  tests and CI). An unset variable is NOT a choice: it binds the SDK-free adapters but is
  refused the `local` relaxations, so a forgotten variable cannot serve a loosened posture.
  An unknown or mis-capitalised value is refused at load, not at the first request.
- **Grounding** (public-web) is OFF by default (`grounding_enabled`).
- **Auth**: managed governed reads support the two exact modes in §6a. A browser supplies the
  human session to IAP and the application re-verifies IAP's signed assertion; S2S uses separate
  IAP and application credentials plus an explicit caller allowlist. Both modes resolve a
  server-verified `Principal`, but a browser is not required to mint a service bearer. Health,
  personas and the governed-RAG manifest stay open. AgentCard/A2A discovery is not published.
  Env prefix `KB_S2S_*` (shared catalog contract CD1).

### 2a. The `local` profile (offline laptop stack)

`local` is a third, fully working deployment option that runs the **whole** `enterprise-knowledge-base` pipeline
end to end with **no Google Cloud, no API key, and no emulator required**. It is what the
dev loop, the test suite, and CI run on. Per-port local backends:

| Concern | `local` backend |
| --- | --- |
| Retrieval (AlloyDB FTS) | `sqlite3` **FTS5** BM25 index over ACL-tagged passages |
| Access control (IAM) | in-process principal to ACL-tag directory (fail-closed) |
| Ingestion (portable parse + GCS) | same portable parser then FTS5 index |
| LLM (Gemini) | deterministic, schema-driven JSON generator (no model, no network) |
| Guardrail (Model Armor) | heuristic blocking injection / jailbreak text |
| PII redaction (DLP) | regex de-identify (SG NRIC/FIN, email, phone) |
| Audit (Cloud Logging WORM) | append-only SQLite, read-back supported |
| Tracer (Cloud Trace) | no-op spans |
| Ledger (AlloyDB) | SQLite freshness + residency table |
| Sessions / Memory / Registry / Tools | in-process stores, seedable |
| Grounding (google_search) | disabled (no web egress) by default |
| Evaluation (deterministic gate) | delegates to the in-repo offline `eval/run_eval.py` |

The default `local` path imports **no** google-cloud package. The platform-client ports
(to `agent-guardrail-gateway`, `agent-registry`, `agent-observability`) under `local` use **in-process** implementations, not HTTP to siblings (a
laptop runs one app, not the whole platform). Optional higher-fidelity local runs route to
Google's official emulators when the standard `FIRESTORE_EMULATOR_HOST` /
`PUBSUB_EMULATOR_HOST` / `STORAGE_EMULATOR_HOST` env vars are set AND the `[gcp]` client
libs are installed (the google client is imported lazily, only on that branch). There is no
emulator for Gemini, Model Armor or DLP, so those stay on the
SDK-free workaround. See `README.md` for the exact "Run locally" seed and command.

## 3. Pinned stack (current GA, mid-2026)

- Python 3.12+, hatchling, ruff (line length 100, select `E,F,I,UP,B,SIM`).
- The product is Gemini Enterprise Agent Platform; host still
  `aiplatform.googleapis.com`.
- Models: reasoning and triage both use `gemini-3.5-flash` against one reviewed Singapore
  single-zone Provisioned Throughput order. Managed calls carry the dedicated-request header;
  Standard PayGo is not an admissible Singapore deployment posture.
  Unified SDK `google-genai`. ADK `google-adk==2.7.1`. A2A v1.0 + MCP 2026-07-28.
- Audit: Cloud Logging locked WORM bucket, retention 2557 days. Tracing: Cloud Trace via
  OpenTelemetry, message-content capture OFF. Eval: deterministic portable gate.
- `[gcp]` extra holds all `google-cloud-*` / `google-adk` / `google-genai` plus
  `google-cloud-storage`,
  `google-cloud-dlp`, `google-cloud-logging`, `google-cloud-alloydb-connector[pg8000]`,
  `sqlalchemy`, `a2a-sdk`, `mcp`. Core deps are framework-light.

## 4. Adapter convention (the build contract)

Every adapter is constructed as `Adapter(settings: Settings)`. The port to dotted-path
bindings in `config/settings.yaml` are the build contract; the contract test reads them.
`gcp` adapters import every `google-cloud-*` / `genai` / `adk` symbol **lazily** (never at
module import time), so the `local` and `onprem` profiles import the whole package with no
GCP SDK.

Ports (all `@runtime_checkable Protocol`):

| Port | gcp adapter | local | platform | onprem |
| --- | --- | --- | --- | --- |
| `RetrievalPort` | AlloyDB PostgreSQL FTS | SQLite FTS5 | | stub |
| `AccessControlPort` | tenant-scoped AlloyDB bindings provisioned from IAM | in-process directory | | stub |
| `IngestionPort` | portable parse + GCS; chunks in AlloyDB | same parse + FTS5 | | stub |
| `CitationStorePort` | AlloyDB chunk table | SQLite `document_chunks` | | stub |
| `FreshnessLedgerPort` | AlloyDB | SQLite | | stub |
| `LLMPort` | Gemini | deterministic generator | | stub |
| `GroundingPort` | google_search | disabled | | benign default |
| `GuardrailPort` | Model Armor | heuristic | `agent-guardrail-gateway` remote | stub |
| `PIIRedactionPort` | DLP | regex | `agent-guardrail-gateway` remote | stub |
| `AuditSinkPort` | Cloud Logging WORM | append-only SQLite | `agent-observability` remote | stub |
| `ObservabilityTracerPort` | Cloud Trace | no-op | | no-op |
| `EvaluationGatePort` | deterministic offline eval gate | offline eval gate | | stub |
| `AgentRegistryPort` | disabled pending trusted runtime context | in-process | `agent-registry` remote | stub |
| `ToolCatalogPort` | MCP catalog | in-process | | stub |
| `AgentRuntimePort` / `SessionPort` / `MemoryPort` | optional and disabled pending trusted context | in-process KB service / stores | | stub |

Every managed AlloyDB adapter uses the same private connector boundary with IAM database
authentication enabled. Terraform creates distinct IAM database logins for the app and pipeline
service accounts; deployment supplies that workload's username as `KB_ALLOYDB_USER`
and Application Default Credentials provide the short-lived token. Missing URI or IAM username is
a managed-preflight failure, never an unauthenticated connection attempt. Application schema and
object privileges are installed by a reviewed schema-owner migration, not by a serving identity.
That migration owns the application tables (`principal_acl_tags`, `documents`, `document_chunks`,
`document_freshness`). The app IAM user receives read-only access; the pipeline user alone
receives table DML. Consequently the managed serving API refuses ingest/delete and
all managed corpus writes run under the pipeline workload. Local keeps those routes for the
offline product demo behind the same domain service.

## 5. Orchestration pipeline (in `domain/`)

`KnowledgeBaseService(retrieval, access_control, guardrail, redaction, llm, tracer, audit,
review_policy=None, answer_policy=None, citation_store=None, citation_policy=None)`:

- `.search(query, actor, acl_principals, tenant, top_k, filters)`:
  `redact(query)` then `access_control.resolve(principals, tenant)` (no tags: access-denied, audit
  ESCALATED) then `retrieval.retrieve` then, **in the domain** (P-09), enforce the caller's
  `tenant` partition (drop other tenants; `""`-tenant passages are shared/global) and
  resolve a shared/tenant duplicate document id to the verified tenant's copy (drop an
  ambiguous id for tenant-less tooling), then
  **filter by allowed tags with all-of / subset matching** (a caller must hold *every* one
  of a passage's tags; untagged passages are dropped fail-closed), then
  `guardrail.screen(OUTPUT)` then `audit.record`.
- `.answer(query, actor, ...)`: runs `search`, then `llm.generate` (structured `{answer,
  used_document_ids, confidence}`), **redacts the generated answer before it reaches the
  anchor resolver, self-critique model, output guardrail, audit or caller**, maps
  `used_document_ids` back to retrieved citations
  (page preserved), **resolves each claim to a layout anchor** (see below), runs a
  self-critique groundedness pass, redacts/screens its caveats, applies `KbReviewPolicy`
  (low confidence or a sensitive ACL tag forces review), screens OUTPUT, audits.

### 5.1 Anchor-level citation

Provenance is a locator, and the locator is as precise as the parse allows:

- **Layout-aware parsing.** The portable parser used by both working profiles extracts PDF/text
  in memory and projects it through `analyze_text_pages` into `LayoutBlock` values. Its splitting,
  classification (`BlockKind`: heading, paragraph, list, table) and character-grid geometry
  are pure and deterministic. `blocks_to_chunks` projects a parse into ordinal-ordered
  `DocumentChunk`s carrying `anchor` (`p{page}#b{index}`) and `bbox`.
- **The anchor model is additive.** `DocumentChunk` and `Citation` gained `anchor` and
  `bbox` (plus `kind` on the chunk); all default to `None`. A citation with no anchor is
  valid, degraded provenance, never an error.
- **The store.** `CitationStorePort.put/get/delete(document_id, tenant, ...)` holds a
  document's anchored chunks in the existing chunk storage. `IngestionService` persists
  what the parser returned on `IngestResult.chunk_anchors`; every method is tenant-scoped
  and fail-closed, so another tenant's anchors resolve to `[]`.
- **Resolution is pure code (`domain/anchors.py`).** The model returns prose and document
  ids only. For each citation, every answer sentence is scored against every stored block
  of that document by content-token coverage; the best pair above
  `policy.citation.anchor_match_floor` (B4, default 0.34) refines the citation's page,
  anchor, box and snippet. Ties break deterministically. An anchor can only come from a
  stored block of the document the citation already names, so resolution narrows a
  locator and never invents one. Below the floor, or with no store bound or reachable, the
  citation is returned unchanged at page level.

The `acl_principals` and `tenant` the service receives are always the server-verified
`Principal`'s (entitlement-checked and never client-widened; see §6), so the ACL and tenant
decisions in the domain are made against a trusted identity.

`IngestionService(ingestion, redaction, guardrail, ledger, tracer, audit,
freshness_policy=None, citation_store=None)`:

- `.ingest(document, content, mime_type, actor)`: `redact(content)` (P-04) then
  `guardrail.screen(INPUT)` (blocked: audit + raise) then `ingestion.ingest` then
  `citation_store.put(anchored chunks)` then `ledger.upsert(freshness/residency)` then
  `audit.record`. PII is redacted **before** indexing. `.delete` removes the document's
  anchors with it, tenant-scoped, so no anchor outlives its evidence.

Policies: `KbReviewPolicy` (P-06), `FreshnessPolicy` (TTL + residency, P-03 / P-07) and
`CitationPolicy` (the anchor match floor).

## 6. `enterprise-knowledge-base` HTTP contract (consumed by sibling repos)

The owned version and compatibility rules are in
[`docs/governed-rag-remote-contract.md`](docs/governed-rag-remote-contract.md). A machine-readable
manifest at `/.well-known/governed-rag-contract` points consumers to the authoritative OpenAPI
schemas; route constants live in `enterprise_kb.api.contract` so clients do not invent competing
names.

- `POST /v1/search {query, top_k, acl_principals[], filters}` to
  `{passages:[{text, citation, score, acl_tags}]}`.
- `POST /v1/answer {query, acl_principals[], filters}` to `GroundedAnswer`.
- `POST /v1/ingest {document_id, title, acl_tags, content_b64, mime_type, ...}` to
  `IngestResult`, and `DELETE /v1/documents/{document_id}`: available in the local demo;
  managed serving returns 403 because corpus writes belong to the pipeline workload.
- `GET /healthz` (reports the active `profile`), `GET /v1/personas` (seeded dev personas
  in the local profile, empty otherwise), and `GET /.well-known/governed-rag-contract`.
  AgentCard/A2A discovery is deliberately not advertised while the verified Agent Runtime
  invocation-context bridge remains unimplemented.

No request carries an `actor`: the audit actor, the entitlement principals and the tenant
partition are all the server-verified `Principal` resolved by the IdentityPort (local dev
personas via `X-Dev-Persona`, or the IAP-injected assertion in secure profiles), never a
client-asserted value. A client `acl_principals` is an entitlement-checked scope-DOWN hint
(`Principal.entitlement_principals`): it may only narrow the verified principal's own
entitlement to a subset, and any id the principal does not hold is dropped, so a caller can
never widen its own visibility by asserting a privileged group. The verified `tenant` scopes
the local write surface too: ingest stamps it server-side and `DELETE` removes only a
same-tenant document (never another tenant's by id). Managed writes have no end-user route and
execute under the pipeline identity. `GET /v1/corpus/status` returns only the caller's tenant
plus shared/global corpus. See `docs/embedding-and-identity.md`.

JSON field names mirror the domain dataclasses (enums as strings) via `to_jsonable`.

### 6a. Authentication: browser and service modes

The governed routes support two exact managed modes. `/healthz`, `/v1/personas` and the
governed-RAG manifest stay open.

- **Browser mode:** IAP authenticates the human and injects its signed assertion. No redundant
  service bearer is required; the application verifies the IAP assertion again.
- **S2S mode:** the caller sends a self-signed IAP JWT in `Proxy-Authorization` with the target
  URL audience and a Google OIDC application token in `Authorization`. IAP consumes the proxy
  credential; the app verifies the OIDC caller allowlist and the IAP assertion. Every allowed
  service email is an explicit `serviceAccount:` IAP accessor and has a reviewed deploy-time
  tenant mapping; an unmapped service account refuses.

Local remains an explicitly selected loopback demo: `X-Dev-Persona` supplies a seeded identity,
and an optional `KB_S2S_TOKEN` adds a constant-time shared-secret check. An unselected profile
inherits neither local relaxation.

### Services `enterprise-knowledge-base` consumes (R1, R2, R4)

- **`agent-guardrail-gateway`** (`GUARDRAIL_GATEWAY_URL` default `:8080`): `POST /v1/guardrail/screen`,
  `POST /v1/redact`.
- **`agent-registry`** (`AGENT_REGISTRY_URL` default `:8083`): `POST /v1/agents`, `GET /v1/agents`.
- **`agent-observability`/audit** (`OBSERVABILITY_URL` default `:8085`): `POST /v1/audit`.

## 6b. Bank-owned policy numbers (the `policy:` section)

Every tunable a compliance owner might change is config, not code. `config/settings.yaml`
`policy:` is parsed into `domain/policy.py::KbPolicy`; the module defaults ARE the values
in the shipped file, so an absent section reproduces reference behavior exactly.

| Setting | Default | Effect |
| --- | --- | --- |
| `policy.review.answer_confidence_floor` | `0.6` | Below this self-critique confidence, an answer escalates to ENHANCED review. |
| `policy.review.review_all_answers` | `true` | The maker-checker floor: every synthesised answer sets `requires_human_review`. Setting it false is a deliberate, recorded deviation. |
| `policy.review.sensitive_tags` | restricted, confidential, pii, mnpi, legal-privileged | ACL labels (case-insensitive substrings) that escalate an answer they ground. |
| `policy.answer.empty_retrieval_raises` | `true` | An answer with no ACL-admitted passage RAISES `RetrievalEmptyError` (HTTP 422) instead of returning an uncited reply. |
| `corpus.ttl_days`, `region` | `7`, `asia-southeast1` | Borrowed by the policy bundle rather than restated: one number, one home. |
| `residency.allowed_regions` | `[asia-southeast1]` | Residency allowlist, validated at app load and mirrored by the Terraform `allowed_regions` variable validated at plan. |
| `pii.jurisdictions` | `SG` | Selects the shared `pii-kit` rows used by the runtime redactor, the DLP custom info types AND the eval `pii_safety` metric. |

Locked consequences of the above:

- **No ungrounded answer.** The orchestrator refuses rather than degrades when nothing the
  caller may see grounds the query. The ESCALATED audit record is written before the
  error propagates.
- **Maker-checker is a floor.** `requires_human_review` is never produced false for a
  synthesised answer; hard signals only raise `review_level` from `standard` to
  `enhanced`, and the reasons are recorded on the answer and in the audit metadata.

## 7. Coding standards

`from __future__ import annotations`, full type hints, frozen `@dataclass(frozen=True,
slots=True)` domain models, pure-stdlib domain, lazy GCP imports, real unit tests driven by
the seeded `local` adapters, a contract test proving both `local` (Protocol parity, runs)
and `onprem` (Protocol parity, fail-fast) bindings, and an offline eval gate. The mandatory
CI gate is `ruff check`, `ruff format --check`, `pytest -m 'not integration'` and an
end-to-end offline answer on the `local` profile with NO Google Cloud SDK installed.
