# Runbook : operating Hrz2 Enterprise Knowledge Base

Operational guide for the `gcp` profile in `asia-southeast1`. For local/dev use the `local`
profile (no credentials, SDK-free adapters via tests/CLI); `onprem` is deliberately fail-fast.

## Managed startup prerequisites

Set `KB_ALLOYDB_URI` and select this workload's username from Terraform output
`alloydb_iam_database_users` as `KB_ALLOYDB_USER` (`app` or `pipeline`). The
connector protects the private path and enables IAM database authentication; Application Default
Credentials are exchanged for a short-lived token, so no database password or shared login is
deployed. Managed preflight refuses before serving when either input is absent or empty.

Run `scripts/apply_managed_schema.sh` from the VPC runner as the separately governed migration
identity before enabling managed serving. It applies 000 to `postgres` and 001 to `enterprise_kb`,
passing output `app` as `app_serving_role` and `pipeline` as `pipeline_role`. The serving identity
receives only schema `USAGE` and table `SELECT`; the pipeline receives scoped DML; neither receives
object ownership or schema creation. Preserve the migration digest and privilege-query output as
deployment evidence. The ordered managed-demo workflow performs this step automatically.

## Health and identity

```bash
curl -s localhost:8082/healthz                       # {status, profile, region}
curl -s localhost:8082/.well-known/governed-rag-contract # deployed peer HTTP contract
```

`/healthz` reports the active profile and the pinned region; if `region` is ever not
`asia-southeast1`, stop and investigate (residency break).

## Corpus freshness

The corpus has a TTL (`corpus.ttl_days`, default 7). The out-of-band refresh job re-indexes
expiring documents so reads rarely pay re-index latency.

```bash
# What is the ledger state?
enterprise-knowledge-base corpus status

# Refresh expiring/missing documents (the daily Cloud Scheduler job runs this):
enterprise-knowledge-base corpus refresh

# Force a full re-ingest of the whole registry (first deploy, or after changing the
# ingestion adapter):
enterprise-knowledge-base corpus refresh --full
```

A document that fails to ingest is written to the ledger as `FAILED` (already expired) so the
next refresh pass retries it. A non-zero exit from the job means at least one document
failed; inspect the per-document log lines.

Freshness records name their `source_authority`. `registry` rows follow the TTL and are
tombstoned when a publisher withdraws them from the reviewed registry. `direct` rows are the
local API/CLI demo surface: no registry can refetch them, so they remain until their owner
explicitly deletes them and a scheduled registry refresh never removes them.

## Citations answer at page level instead of anchor level

Symptom: answers cite `document p.7` with no block anchor, and the eval gate's
`citation_accuracy` drops. Anchor resolution degrades silently by design (provenance is
never withheld because the anchor store is unhappy), so check in this order:

1. **Are anchors stored for that document?** They are written at ingest, so a document
   ingested before layout-aware parsing has none until it is re-ingested. Fix with
   `enterprise-knowledge-base corpus refresh --full`, which re-parses and REPLACES each document's
   anchors.
2. **Is the citation store reachable?** On `gcp` it is the AlloyDB chunk table
   (`alloydb.vector_table`); on `local` the `document_chunks` table in `local.db_path`. A
   read failure is swallowed and logged as a degraded answer, never raised at the caller.
3. **Is the caller's tenant the document's tenant?** The store is tenant-scoped and
   fail-closed: a cross-tenant read returns no anchors, which is correct.
4. **Is the match floor too high?** `policy.citation.anchor_match_floor` (default 0.34) is
   the share of a claim's content vocabulary the block must contain. Raising it withholds
   more anchors; it never makes a wrong anchor right.

Anchored counts are on every answer audit record as `n_anchored`, so the ratio is
measurable from the audit log without re-running anything.

## Access-control issues ("I cannot see a document")

1. Confirm the caller's principals resolve to tags: a principal with no tags sees nothing
   (this is correct, and audited as ESCALATED with `access_denied=true`). Remember matching
   is all-of / subset: the caller must hold *every* one of a passage's tags, so holding one
   of a multi-tag document's labels is not enough.
2. Confirm the caller's tenant matches the document's: a passage in another tenant is dropped
   by `filter_by_tenant` (only same-tenant and shared/global `""`-tenant passages are
   admissible). A passage with no tags is never returned (fail-closed, P-09).
3. Check that a client `acl_principals` scope-down hint is not narrowing too far: it is
   entitlement-checked and can only select a subset of the verified principal's own groups.
4. On the gcp profile, query `principal_acl_tags` with both the verified `tenant` and the
   verified principal id. Only `enabled IS TRUE` rows are effective. Never diagnose with a
   tenant-free query: it can hide a cross-tenant provisioning error. The reviewed ACL JSON in
   `gs://<control-bucket>/acl/` is the authority: refresh atomically replaces the binding
   projection before corpus work and refuses missing/malformed input. Pipeline code can read but
   not rewrite that publisher-owned object; serving only reads AlloyDB. On local, check the
   synthetic in-process mapping.

The managed refresh job is deliberately single-task and takes a session-owned AlloyDB advisory
lease before downloading ACL or registry authority. A second execution refuses immediately; a
crashed worker releases the lease with its database connection. Do not remove this lease or move
artifact download ahead of it: doing so reintroduces stale ACL/index writers.
Before any managed download, object metadata is checked against the fixed allocation ceilings:
1 MiB registry, 1 MiB ACL authority and 50 MiB source document. GCS downloads are byte-ranged and
bound to the inspected generation; HTTP sources stream and stop at the same document ceiling. An
over-limit artifact is an input-governance failure—replace it with a smaller reviewed artifact.

The ACL decision is in the domain (`filter_by_tenant` + `filter_by_allowed_tags`), never in
the retrieval adapter, so debugging starts with the resolved tag set and the caller's tenant,
not a retired or unsupported retrieval service.

## Audit and trace

Every search / answer / ingest writes an already-redacted `AuditEvent` to the locked WORM
Cloud Logging bucket (retention ~7 years). Query it by label
(`action`, `actor`, `decision`, `resource`). Trace spans carry ids and metadata only:
message content capture is OFF, so no prompt, passage, or answer text ever lands on a span
(P-04).

## Incident: suspected PII leak

1. PII is redacted at every content boundary: ingest before storage/model use, query ingress,
   generated answer before anchoring/self-critique/output, and self-critique caveats. Confirm the redaction adapter is
   the real DLP adapter (gcp) and not a stub (onprem raises, never passes through).
2. Audit records and trace spans are already redacted by construction; if raw PII appears in
   either, the redaction port returned unredacted text. Check the inline jurisdiction pack in
   `dlp_redaction` and the `RedactionResult.findings`.
3. The eval `pii_safety` metric (and `acl_correctness`) gate every release; a regression
   here blocks promotion. `pii_safety` reads the same jurisdiction rows as the runtime
   redactor (`pii.jurisdictions` in `config/settings.yaml`), so widening the market means
   updating that one setting, not the adapter.

## Eval gate (pre-promotion)

```bash
python eval/run_eval.py            # offline, no credentials
python eval/run_eval.py            # deterministic promotion authority in every profile
```

A release must not be promoted to any managed serving channel unless the gate is green.
Agent Runtime additionally remains blocked pending a verified context bridge. `acl_correctness`
(>= 0.99) is the non-negotiable bar: a single forbidden document returned is a hard failure.
`citation_accuracy` (>= 0.98) is scored at anchor level against the golden anchors in
`eval/datasets/golden_kb.jsonl`; if it fails, compare the resolved anchor with the declared
one before touching the threshold, and never lower the threshold to get green.

## Deploy / rollback

The supported release path is `.github/workflows/managed-demo-release.yaml`; see the exact
foundation variables, identities and prerequisites in `infra/terraform/README.md`. It is split at
two approval boundaries:

1. `managed-bootstrap` targets only APIs, regional KMS, its Artifact Registry service-agent key
   binding, the CMEK repository and the scoped image publisher. It builds three images and retains
   their immutable digests; it cannot deploy an app.
2. `managed-release` consumes those digests in a full apply. It provisions a Singapore regional
   external managed ALB/IAP edge and private Cloud Run UI/API, migrates AlloyDB from the VPC runner,
   publishes separately owned fictional raw and registry/ACL inputs, and waits for the initial
   refresh. Open the workflow's `managed_api_url` through IAP as the exact reviewed demo user; do
   not use `X-Dev-Persona`, which exists only for an explicitly selected local loopback profile.

The optional Agent Runtime code seam has no deployed instance, service account, IAM grant, KMS
grant or database login. It remains fail-closed pending a verified invocation-context bridge and
is not part of managed release readiness.

### Authenticated managed-journey smoke evidence

After the full release, schema migration, authority publication and initial refresh complete,
run the live smoke against the IAP-protected origin. The three inputs are external evidence, not
repository defaults:

```bash
export KB_MANAGED_BASE_URL="$(terraform -chdir=infra/terraform output -raw managed_api_url)"
export KB_MANAGED_IAP_ID_TOKEN="<short-lived token minted for this deployment's IAP OAuth client id>"
export KB_MANAGED_EXPECTED_DOCUMENT_ID="cloud-onboarding-policy"
pytest tests/integration/test_gcp_smoke.py -q -m integration
```

The token must belong to an exact reviewed `iap_accessors` identity. Its verified email and
derived tenant must match a binding in the published ACL authority; the token must be short-lived,
must contain no literal `Bearer ` prefix, and must never enter source, Terraform state, workflow
artifacts or test output. The smoke refuses non-HTTPS origins, traverses IAP, and proves the
deployed API reports the managed Singapore profile, the expected document is fresh for that
identity, search returns it, and answer cites it with the maker-checker flag intact. Retain the
test result with the release evidence; the offline gate only deselects this integration test.

The recoverable demo posture leaves the WORM bucket unlocked; named production requires separately
reviewed `production_mode=true` and `lock_worm_bucket=true`. The lock and KMS key protection are
irreversible. Roll back the API/UI by applying prior immutable digests; the corpus and ledger remain
unaffected. Do not roll Terraform state back or destroy the KMS key. If ingestion evidence is bad,
stop the scheduler/refresh job first and withdraw or correct the publisher-owned control registry.
