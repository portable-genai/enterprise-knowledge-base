# `enterprise-knowledge-base` : Terraform (asia-southeast1)

Concrete, Singapore-resident infrastructure for the managed (`gcp`) profile. This reference
module is deliberately pinned to Singapore while the core/adapters remain region-parametric;
reviewed deployment inputs include immutable images, IAP accessors and control artifacts;
per-tenant values (org/billing ids and the VPC-SC toggle) are variables. The application
talks to ports, never to these resources directly (P-02), so this stack can be replaced
wholesale by an on-prem equivalent without touching the domain.

## What gets created

| File | Resources | Principle |
| --- | --- | --- |
| `apis.tf` | Enables exactly the services `enterprise-knowledge-base` uses | P-01 |
| `kms.tf` | One regional CMEK key ring + key, per-service key bindings | P-03, P-09 |
| `cloud_storage.tf` | Separate CMEK raw-source, publisher-owned control, and redacted-output buckets with bucket-scoped IAM | P-03, P-04, P-09 |
| `alloydb.tf` | Private AlloyDB cluster + primary + per-workload IAM database users | P-03, P-05, P-09 |
| `model_armor_network.tf` | Exact-host regional PSC + private DNS for Model Armor and DLP | P-03, P-04 |
| `artifact_registry.tf` | Regional CMEK Docker repository and scoped CI publisher | P-01, P-06, P-09 |
| `logging_worm.tf` | Singapore CMEK `_Default`/`_Required`, governed-audit exclusion, locked WORM sink | P-03, P-08, P-09 |
| `observability_foundation.tf` | Read-only effective check for Singapore CMEK Cloud Trace storage | P-03, P-09 |
| `iam.tf` | Least-privilege app, pipeline, migration and scheduler service accounts | P-06, P-09 |
| `vpc_sc.tf` | VPC Service Controls perimeter around the AI/data APIs | P-03 |
| `org_policy.tf` | Residency, private-IP, uniform-access and service-account-key denial policies | P-03, P-05, P-06 |
| `managed_api.tf` | Singapore regional external managed ALB/IAP edge + same-origin Cloud Run UI/API | P-01, P-03, P-09 |
| `agent_runtime.tf` | Explicit no-authority boundary while Agent Runtime deployment is disabled | P-01, P-09 |
| `scheduler.tf` | Cloud Run job + Cloud Scheduler for the freshness refresh | P-03 |

## Two-phase managed demo release

Follow the two-phase procedure in [`../../docs/runbook.md`](../../docs/runbook.md); do not
bootstrap a managed demo by running a `gcp` process on localhost. The procedure keeps two
approval boundaries and never stores a service-account key or database password. It used to be
carried by a GitHub Actions workflow, which never ran because Actions were disabled
organization-wide at the time; the boundaries are now enforced by the operator following the
runbook, not by a protected environment, so treat the approval gates as a manual control until a
GitHub Actions release workflow replaces them.

Foundation prerequisites, created outside this stack:

1. A versioned, CMEK-protected Singapore GCS Terraform-state bucket with uniform access and no
   public access. The workflow supplies it to the checked-in empty `backend "gcs" {}` using
   `TERRAFORM_STATE_BUCKET`; this cannot safely be created in the state it coordinates.
2. Organization or folder Logging defaults that set the project location to
   `asia-southeast1` **before the deployment project is created**. Google creates `_Default` and
   `_Required` with the project, and their location is immutable. Terraform imports those exact
   Singapore bucket ids and reconciles their CMEK; a pre-existing global project therefore fails
   rather than being presented as resident. Create a fresh project under the correct defaults if
   either system bucket is elsewhere.
3. A public Cloud DNS managed zone authoritative for `api_domain`, and reviewed Workload Identity
   Federation bindings for the deploy, image publisher, migration, raw-source publisher and
   control-authority publisher identities.
4. A VPC-connected runner labelled `self-hosted`, `linux`, `hrz2-vpc` with Docker, `gcloud`
   563 or newer,
   `psql`, `alloydb-auth-proxy`, `jq` and `sha256sum`. Every release job runs there: a public
   GitHub runner is outside an enforced VPC-SC perimeter and cannot safely bootstrap state,
   publish images/control artifacts or start the refresh job.
5. A reviewed Singapore single-zone Gemini 3.5 Flash Provisioned Throughput order. Set
   `gemini_single_zone_pt_confirmed=true` only when that capacity is active.

Configure both `managed-bootstrap` and `managed-release` GitHub environments with required
reviewers. Repository/environment variables are `GCP_PROJECT_ID`, `TERRAFORM_STATE_BUCKET`,
`WIF_PROVIDER`, `DEPLOY_SERVICE_ACCOUNT`, `ARTIFACT_PUBLISHER_SERVICE_ACCOUNT`,
`MIGRATION_SERVICE_ACCOUNT`, `SOURCE_PUBLISHER_SERVICE_ACCOUNT` and
`CONTROL_PUBLISHER_SERVICE_ACCOUNT`. Store a reviewed JSON rendering of
`terraform.tfvars.example` as `TERRAFORM_TFVARS_JSON`; it contains no credential or secret value,
but keeping it as an environment secret prevents accidental log expansion. Its three publisher
emails must exactly match the workflow identities, and `iap_accessors` must include the dispatched
demo user as `user:<email>`.

Dispatch **Managed demo release** with an auditable lowercase `release_id` and the exact IAP demo
user. The renderer derives the ACL tenant from that verified user's email domain, matching the IAP
adapter's documented human-user fallback (and the Workspace `hd` value for the reviewed account);
it does not accept an unrelated caller-supplied tenant. The sequence is executable and ordered:

1. **Phase 1, bootstrap/build:** a targeted Terraform apply enables APIs and creates regional KMS,
   the KMS-bound Artifact Registry repository and its repository-scoped publisher. No app resource
   is targeted. The publisher builds and pushes API/UI/refresh images and emits exact digests.
   Before approving Phase 2, the platform owner sets the Observability project default location
   and regional CMEK to the Phase 1 key using the commands below. This is an explicit project
   foundation hand-off, not an application default.
2. **Phase 2, deploy/prove:** after `managed-release` approval, full Terraform apply consumes only
   those digests and provisions the private Cloud Run service before its regional serverless NEG.
   On a fresh project that first edge apply uses the fail-closed `bootstrap-pending` IAP audience;
   the workflow reads the generated backend numeric id and performs a mandatory second apply to
   converge the exact signed-header audience before any schema or corpus step. It then completes
   the Singapore regional ALB/IAP/DNS edge and private workloads, and the
   VPC runner applies SQL 000/001 with IAM DB auth. Separate source and control publishers upload
   the reviewed fictional raw source and rendered registry/ACL authority, the workflow waits for
   the initial refresh, and its summary prints the IAP browser URL and prompt.

The standalone `build-managed-images.yaml` and `schema-migration.yaml` workflows remain useful for
independent image and migration maintenance; the managed demo uses the ordered workflow above so
none of the prerequisites can be silently skipped.

Between the two approvals, initialize the current Observability foundation with the Phase 1 key:

```bash
KMS_KEY="$(terraform -chdir=infra/terraform output -raw kms_key)"
gcloud beta observability settings update \
  --project="$GCP_PROJECT_ID" \
  --location=global \
  --default-storage-location=asia-southeast1 \
  --update-mask=defaultStorageLocation
gcloud beta observability settings update \
  --project="$GCP_PROJECT_ID" \
  --location=asia-southeast1 \
  --kms-key-name="$KMS_KEY" \
  --update-mask=kmsKeyName
```

The full plan invokes `scripts/check_managed_observability_foundation.sh` read-only. It refuses a
different default location/key, multiple `_Trace` buckets, or an existing `_Trace` bucket whose
location or key differs. The recoverable demo permits `_Trace` to be absent before the first span;
`production_mode=true` requires one effective Singapore CMEK `_Trace` bucket. After the first
smoke request, rerun the plan and retain `gcloud beta observability buckets list --location=-`
output as named-project evidence.

For operator diagnostics after apply, export the outputs into the app's runtime environment so
`config/settings.yaml` resolves them (the output names map onto the settings fields):

```bash
export KB_CORPUS_BUCKET=$(terraform output -raw corpus_bucket)
export KB_CONTROL_BUCKET=$(terraform output -raw control_input_bucket)
export KB_RAW_SOURCE_BUCKET=$(terraform output -raw raw_source_bucket)
export KB_ALLOYDB_URI=$(terraform output -raw alloydb_instance_uri)
export KB_ALLOYDB_USER=$(terraform output -json alloydb_iam_database_users | jq -r .app)
export KB_KMS_KEY=$(terraform output -raw kms_key)
```

Use the matching key from `alloydb_iam_database_users` for each deployment: `app` for the API,
`pipeline` for ingestion/refresh. These are usernames, not
secrets. Each connector enables IAM auth and exchanges that workload's Application Default
Credentials for a short-lived token. AlloyDB adapters refuse an absent or empty URI or
username before opening a connection. Terraform contains no database password, shared login or
secret-version payload.

Terraform cannot connect to PostgreSQL to bootstrap the database or grant privileges. Before
serving, run `scripts/apply_managed_schema.sh`: it uses the dedicated passwordless migration IAM
identity to apply audited `000` against `postgres`, then `001` against `enterprise_kb`. Map outputs
keys to its variables as follows: `app` -> `app_serving_role` and `pipeline` ->
`pipeline_role`. The migration revokes `PUBLIC`, grants the serving role only
`USAGE`/`SELECT`, and grants the pipeline role scoped DML.

## Fail-fast residency

`variables.tf` takes `region` as a deploy-time input and validates it at plan time against
`allowed_regions`, the residency allowlist. Both default to `asia-southeast1`, so an unset deploy
stays in Singapore; deploying elsewhere means setting both, which is the deliberate residency
review point. It then validates immutable images and regional Artifact
Registry prefixes; registry/ACL artifacts must live in the publisher-owned control bucket and raw
documents in the read-only source bucket. Org Policy
and dry-run-first VPC-SC are defence-in-depth backstops. Retrieval remains in private AlloyDB;
there is no unsupported-region search-service fallback.

The stack also enforces project Org Policies that disable both service-account key creation and
service-account key upload. WIF, IAM database authentication and materialized Google service
identities replace long-lived or synthesized credentials. In particular, Direct VPC
`roles/compute.networkUser` is granted to the API-materialized Cloud Run service identity.
Cloud Logging is the exception to the generic service-identity pattern: log-bucket CMEK uses the
project Settings API's exact `kmsServiceAccountId`. Terraform refuses a legacy `cmek-p...`
identity when VPC-SC is enabled and directs the operator to complete Google's CMEK identity
migration first. Workload app/pipeline service accounts have no raw KMS role; storage, database,
runtime, registry and logging service agents perform their own envelope encryption.

The pipeline is viewer-only on raw and control inputs and writes only the redacted projection.
Separate reviewed source and control publisher service accounts own raw document uploads and
registry/ACL changes respectively; neither authority is granted to serving or refresh workloads.
The regional external managed Application Load Balancer, both backend services, IAP IAM resources,
URL map, HTTPS proxy, address and forwarding rule all carry `var.region`; a proxy-only subnet and
regional Certificate Manager certificate keep the edge family in Singapore. Model Armor and DLP
PSC endpoints target the private `.p.rep.googleapis.com` services while exact private DNS records
map the public regional `.rep.googleapis.com` names clients use.

One IAP string is intentionally not a regional resource path: the signed-header JWT contract uses
`/projects/<number>/global/backendServices/<id>` for Compute backends. `KB_IAP_AUDIENCE` therefore
uses that documented global-format `aud` with the **regional backend's generated numeric id**;
regional backend management and IAM remain region-scoped. During the named deployment, compare the
Terraform output with IAP console **Signed Header JWT Audience** and retain that proof before the
browser journey is accepted.
Do not leave `iap_backend_service_id=bootstrap-pending`: it rejects all genuine IAP assertions and
exists only to break the fresh-project service-before-NEG/backend-ID ordering. The ordered release
workflow always replaces it with `managed_api_backend_service_id` before proceeding.

## Irreversible steps (read before apply)

- **WORM lock** (`logging_worm.tf`): the recoverable demo default is
  `production_mode=false` and `lock_worm_bucket=false`. A named production approval sets both
  true; Terraform refuses production mode without that explicit confirmation. Once locked, the
  bucket is Write-Once for the full retention window (~7 years) and cannot be unlocked, even by
  a project owner. The same production gate also requires enforcing (not dry-run) VPC-SC and the
  reviewed Singapore Gemini Provisioned Throughput order; the recoverable demo keeps those
  irreversible/enforcing transitions explicit.
- **KMS key** (`kms.tf`): `prevent_destroy = true`. A destroyed key strands all
  CMEK-encrypted data.
- **VPC-SC** (`vpc_sc.tf`): the perimeter defaults to dry-run. Review violation logs and access
  levels before setting `vpc_sc_dry_run = false`; do not disable the control to bootstrap.
- **System log buckets** (`logging_worm.tf`): `_Default` and `_Required` must already be in
  Singapore. Their location cannot be changed after project creation. The managed exclusion drops
  only `enterprise-knowledge-base-audit` from `_Default`; the dedicated WORM sink still receives that log and
  `_Required` is unaffected.
- **Managed input limits:** registry and ACL authority are each limited to 1 MiB; an individual
  source document is limited to 50 MiB. GCS size and generation metadata are verified before a
  bounded download, and HTTP sources stream under the same document ceiling. Publish a smaller,
  reviewed artifact rather than raising these limits casually: they bound Cloud Run allocation.

## Not created here (by design)

- The Agent Runtime (`reasoningEngine`) instance or identity. The optional code seam remains
  fail-closed until a trusted SDK invocation-context bridge exists; Terraform grants it no service
  account, IAM role, KMS access, database login or network authority.
- The `enterprise_kb` database and application schemas. Apply the auditable 000/001 workflow.
  That migration creates ACL, citation/chunk and freshness tables, revokes `PUBLIC`, grants
  app read-only access and grants DML only to the pipeline. Runtime adapters issue no DDL;
  managed serving rejects ingest/delete while the pipeline owns corpus mutations.
- Terraform does not build images. Phase 1 of the release workflow uses WIF, pushes API/UI/refresh
  images to the Terraform-managed CMEK repository and retains exact digest inputs; Terraform
  rejects mutable tags and any other repository.

`terraform` is never run as part of the test/lint gate; this directory is reference infra.
