# Compliance : principle-to-control mapping (Hrz2 Enterprise Knowledge Base)

> **Authority:** SPEC > ARCHITECTURE > COMPLIANCE > README > `docs/`. See [`docs/doc-authority.md`](docs/doc-authority.md).

This maps the catalog's General Principles (P-01..P-12) and dependency rules (R1..R6) to
the concrete controls implemented in **this** repo. Hrz2 is the concrete home of **P-03**
(single-region governed corpus), **P-04** (redact before index and serve), **P-05**
(grounding over fine-tuning, it IS the governed RAG), **P-07** (citations + audit), and
ACL least-privilege (**P-09**). Where a principle does not apply to Hrz2, it is marked n/a
with a reason.

## A. General Principles (P-01..P-12)

| ID | Principle | Where it lives in Hrz2 | Evidence |
| --- | --- | --- | --- |
| P-01 | Managed-first, minimal surface | AlloyDB FTS/ACL/freshness, regional GCS, Gemini, Model Armor and DLP are the managed backends; `apis.tf` enables exactly those | `infra/terraform/apis.tf`, `config/settings.yaml` |
| P-02 | No vendor lock-in (ports & adapters) | Pure domain talks only to `Protocol` ports; `gcp` / `local` / `platform` / `onprem` adapter families; one-line `KB_PROFILE` switch. The WORKING `local` family runs the whole pipeline off-cloud (SQLite FTS5 + deterministic LLM, no GCP SDK), a concrete demonstration that the domain is not bound to any vendor | `src/enterprise_kb/ports/`, `adapters/local/`, `adapters/onprem/`, `tests/contract/` |
| P-03 | Data residency (single region) | Region pinned `asia-southeast1`; single-region corpus bucket and AlloyDB; freshness records carry region; org-policy + VPC-SC backstop | `cloud_storage.tf`, `alloydb.tf`, `org_policy.tf`, `vpc_sc.tf`, `domain/freshness_policy.py` |
| P-04 | Minimise PII to model / store | Managed registry/ACL/source reads have metadata-first byte limits; DLP redaction runs at ingest before indexing, at query ingress, and immediately after answer generation before anchoring, self-critique, output guardrail, audit or return; critique caveats are redacted/screened too. DLP uses finding-bounded chunks with boundary overlap; trace content capture is OFF | `pipelines/fetch.py`, `pipelines/acl_sync.py`, `domain/ingestion_service.py`, `domain/kb_service.py`, `adapters/gcp/dlp_redaction.py`, `adapters/gcp/cloud_trace_tracer.py` |
| P-05 | Grounding over fine-tuning | Hrz2 IS the governed RAG; answers are synthesised only from retrieved, permitted passages, never beyond the set; self-critique pass | `domain/kb_service.py`, `domain/prompts.py` |
| P-06 | Maker-checker (human-in-the-loop) | `KbReviewPolicy`: low-confidence or sensitive-ACL answers require human review; the API/UI surface the flag | `domain/hitl.py`, `api/schemas.py`, `ui/components/ui.tsx` |
| P-07 | Everything cited + audited | `Citation` on every passage/claim, resolved to an ANCHOR (layout block + bounding box) where the parse supports it and to the page otherwise; every search/answer/ingest writes a WORM `AuditEvent` recording how many citations were anchored | `domain/kernel.py`, `domain/anchors.py`, `domain/layout.py`, `domain/kb_service.py`, `adapters/gcp/cloud_logging_audit.py` |
| P-08 | Quality / model-risk gate | Deterministic offline eval gate (`retrieval_recall`, `acl_correctness>=0.99`, anchor-level `citation_accuracy>=0.98`, `pii_safety>=0.99`) is the promotion authority in every profile | `eval/run_eval.py`, `eval/rubrics/citation_accuracy.yaml`, `eval/datasets/layout/` |
| P-09 | Least privilege / governed access (ACL) | Domain tenant/all-of tag checks fail closed; a tenant-owned duplicate document id shadows its shared copy and tenant-less ambiguity is dropped. Refresh replaces the AlloyDB binding projection from reviewed versioned JSON in a publisher-owned control bucket it can only read; one database advisory lease serializes ACL, registry, index and ledger mutations. Serving only reads the projection. Distinct passwordless IAM DB users and migration grants keep serving read-only and pipeline DML scoped. Client principals only narrow verified scope. Browser IAP and S2S dual-token modes derive identity/tenant server-side | `domain/_grounded.py`, `pipelines/acl_sync.py`, `pipelines/refresh_job.py`, `api/security.py`, `adapters/gcp/iam_access_control.py`, `infra/sql/001_principal_acl_tags.sql`, `infra/terraform/cloud_storage.tf` |
| P-10 | Observability / FinOps | Cloud Trace spans (content OFF) around each pipeline; token-usage metrics for FinOps | `adapters/gcp/cloud_trace_tracer.py`, `domain/_grounded.py` (`maybe_record_usage`) |
| P-11 | Freshness / lifecycle | TTL ledger; retrieval joins only fresh/non-expired rows; refresh hides old projections before fetch, re-ingests expired sources and tombstones registry removals. A crash-releasing AlloyDB advisory lease and single-task job prevent overlapping refresh/ACL writers | `adapters/gcp/alloydb_retrieval.py`, `pipelines/ingest.py`, `pipelines/refresh_job.py`, `infra/terraform/scheduler.tf` |
| P-12 | Reversibility (exit / on-prem) | Two off-cloud families prove reversibility: a WORKING `local` stack (runs end to end off-cloud, the documented exit-rehearsal) and a fail-fast `onprem` family (Google Distributed Cloud target, exits 2 with the migration message). The contract test proves both satisfy every port with no GCP SDK | `adapters/local/`, `adapters/onprem/`, `tests/contract/test_port_parity.py`, `docs/onprem-migration.md` |

## B. Dependency rules (R1..R6)

| ID | Rule | Hrz2 status | Control |
| --- | --- | --- | --- |
| R1 | Use Hrz1 Guardrail for screening + redaction | **Applies.** Hrz2 ingests documents that may carry PII; Hrz1 redaction/screening runs at the ingest and serve boundary | `GuardrailPort` / `PIIRedactionPort`; `adapters/platform/remote_guardrail.py`, `remote_redaction.py` |
| R2 | Audit to Hrz5 Observability | **Applies.** Every interaction writes an `AuditEvent` to Hrz5 (or local WORM) | `AuditSinkPort`; `adapters/platform/remote_audit.py` |
| R3 | Use the Hrz2 governed store | **It IS Hrz2.** Hrz2 is the governed store other agents query; it does not consume itself | this repo |
| R4 | Register in Hrz3 Registry | **Open integration gate.** The deployed HTTP peer contract is discoverable, but AgentCard registration is deliberately disabled until Agent Runtime has a trusted invocation-context bridge | `api/contract.py`, `managed_readiness.tf`, `agent/root_agent.py` |
| R5 | Use the Hrz4 eval gate before promotion | **Applies.** Hrz2 ships a deterministic in-repo gate; Hrz4 consumes the same report contract | `eval/run_eval.py`, `adapters/local/evaluation.py` |
| R6 | Residency / sovereignty controls | **Applies.** Single-region store, CMEK, VPC-SC, org policy | `infra/terraform/` |

## C. How the controls compose in one request

A `POST /v1/answer` for principal `group:risk`:

0. Authenticate the selected mode (P-09): a browser supplies the verified IAP assertion without a
   redundant service bearer; S2S supplies IAP `Proxy-Authorization` plus an allowlisted app OIDC
   bearer. The `IdentityPort` resolves only server-verified principals and service tenant mappings.
   The request body carries no actor; an unresolved principal is a 401. The entitlement
   principals and the tenant fed to retrieval are the verified principal's: a client
   `acl_principals` is entitlement-checked (`entitlement_principals`) and can only narrow to
   a subset the principal already holds, never widen.
1. Redact the query (P-04) before it reaches retrieval or the audit log.
2. Resolve the entitlement principals to their ACL tags via `AccessControlPort` (P-09). No
   tags: access denied, audit ESCALATED.
3. Retrieve fresh candidates from AlloyDB FTS after SQL tenant/all-of ACL pushdown, then **in the domain** (P-09) enforce the
   caller's tenant partition (other tenants dropped; `""`-tenant passages are shared/global)
   and **filter with all-of / subset tag matching** (the caller must hold every one of a
   passage's tags); a passage with no tag is dropped (fail-closed).
4. Screen the rendered output (R1), synthesise an answer from the permitted passages only
   (P-05), redact the generated prose before any second model/outward boundary, and map
   citations back with page provenance (P-07).
5. Self-critique groundedness, then redact/screen its caveats; `KbReviewPolicy` flags low-confidence or sensitive-ACL
   answers for human review (P-06).
6. Screen OUTPUT again (R1) and write an already-redacted WORM `AuditEvent` to Hrz5 (R2, P-07)
   inside a Cloud Trace span with content capture OFF (P-04, P-10).

## D. Verification

- `ruff check`, `ruff format --check`, `pytest -m 'not integration'`, the offline eval gate,
  and an end-to-end offline answer on the `local` profile (no GCP SDK) are the mandatory
  gate.
- `tests/contract/test_port_parity.py` proves both the `local` family (Protocol parity,
  runs offline) and the `onprem` family (Protocol parity, fail-fast) satisfy every port
  (P-02, P-12).
- `tests/unit/test_kb_service.py` proves ACL filtering happens in the domain, fail-closed
  on untagged passages, redact-before-retrieval, and the review gate (P-04, P-06, P-09).
- `python eval/run_eval.py` gates on `acl_correctness >= 0.99` (P-08, P-09).

## E. Regulator crosswalk (adopter-owned)

**Who owns this appendix: the adopting bank, not this repository.** The rows below are a
worked TEMPLATE for the home regulator (MAS, Singapore), filled in so an adopter can see
the shape and the level of evidence expected. They are an engineering reading of public
guidance, not legal advice and not a certification. Replace the left column with your own
regulator's obligations, re-point the evidence column at your deployment, and have your
compliance function sign the result. Upstream will keep the CONTROL and EVIDENCE columns
current; it will never maintain your regulator's column for you.

Evidence paths are relative to this repository. A row whose control is "posture only"
says so: it is proved by code and configuration here, and its live enforcement needs your
deployment's evidence.

| Home regulator obligation (MAS, template) | This repo's control | Evidence | Live evidence you must add |
| --- | --- | --- | --- |
| MAS TRM 6: data residency and sovereignty of customer data | Region pinned and validated against a residency allowlist at `terraform plan` AND at app load; `gcp.resourceLocations` Org Policy generated from the same allowlist; regional CMEK; VPC-SC perimeter (dry-run first) | `infra/terraform/variables.tf`, `org_policy.tf`, `kms.tf`, `vpc_sc.tf`; `src/enterprise_kb/config.py` `_validate_residency`; `tests/unit/test_residency_posture.py` | Org Policy and perimeter enforcement in the named project; CMEK key state |
| MAS TRM 8: access control, least privilege, segregation | Object-level authorization in the domain with all-of tag matching and a tenant partition; server-verified identity; a client may only narrow its scope | `src/enterprise_kb/domain/_grounded.py`, `src/enterprise_kb/domain/identity.py`, `tests/unit/test_kb_service.py`, `tests/unit/test_api_identity.py` | IdP/IAP registration and group provisioning |
| MAS TRM 11: audit trail, tamper evidence, retention | SHA-256 hash-chained append-only audit with export/restore verification; WORM log bucket with a 2557-day retention floor | `src/enterprise_kb/adapters/local/audit.py`, `infra/terraform/logging_worm.tf`, `tests/unit/test_audit_chain.py` | Bucket lock applied; retention and alerting observed in the live project |
| MAS 626 / PDPA: personal data minimisation and de-identification | Redact-before-everything at both pipeline entrances; jurisdiction-selected PII pack shared by the runtime redactor, inline regional DLP request and eval gate | `src/enterprise_kb/pii_patterns.py`, `src/enterprise_kb/adapters/local/redaction.py`, `adapters/gcp/dlp_redaction.py`, `tests/unit/test_pii_jurisdictions.py` | Regional DLP PSC/call evidence; your jurisdiction list signed off |
| MAS FEAT (fairness, ethics, accountability, transparency): explainability of an automated output | Every claim carries a `Citation` bounded by the retrieved set and resolved, in deterministic code, to the layout block and bounding box a reviewer opens; an ungrounded answer is refused, not softened | `src/enterprise_kb/domain/kb_service.py`, `src/enterprise_kb/domain/anchors.py`, `eval/rubrics/citation_accuracy.yaml`, `tests/unit/test_anchor_resolution.py` | Your model card and business sign-off |
| MAS FEAT / TRM 13: human accountability for a consequential output | Maker-checker floor: every synthesised answer carries `requires_human_review`, hard signals raise the level to enhanced, nothing auto-executes | `src/enterprise_kb/domain/hitl.py`, `tests/unit/test_policies.py` | Your reviewer roster and SLA in Hrz7 |
| MAS Outsourcing / TRM 5: exit and portability from a cloud provider | Ports-and-adapters with three profiles and a bounded, executable portability proof | `scripts/portability_demo.py`, `docs/onprem-migration.md`, `tests/contract/test_behavioral_parity.py` | Your tested exit rehearsal |

### How to fork this appendix for another regulator

1. Copy the table, replace the first column with your obligations, and keep the control
   and evidence columns; they are the parts upstream maintains.
2. Delete any row whose control you have replaced, and add a row for every control you
   added. A row with no evidence path is not a control.
3. Record the sign-off (who, when, against which repository version) alongside the table.
   This repository deliberately does not carry your sign-off.
