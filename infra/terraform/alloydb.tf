# alloydb.tf : AlloyDB freshness/residency ledger + chunk vector store.
#
# General Principle map:
#   P-03 (residency): cluster + primary instance pinned to asia-southeast1.
#   P-05 (private-only data plane): the instance has NO public IP; it is reachable only
#         over the VPC via Private Service Access (PSA). ip_type=PRIVATE in
#         config/settings.yaml is honoured here at the infra layer.
#   P-09 (CMEK explicit): encryption_config.kms_key_name set to the regional key (CMEK
#         does not cascade : AlloyDB needs its own key binding, granted in kms.tf).
#
# The freshness ledger table (document_freshness) and the chunk vector table
# (document_chunks) are created by the app/migration, not Terraform; here we provision the
# cluster, the PRIVATE primary, and the network.

# ---- VPC + Private Service Access range for the private AlloyDB endpoint ---- #
resource "google_compute_network" "kb" {
  name                    = var.vpc_network_name
  auto_create_subnetworks = false
  project                 = var.project_id

  depends_on = [google_project_service.required]
}

# Direct VPC egress for Cloud Run jobs. /26 is the smallest supported subnet; Private Google
# Access keeps managed API calls on Google networking while ALL_TRAFFIC routes through this VPC.
resource "google_compute_subnetwork" "serverless" {
  name                     = "enterprise-knowledge-base-serverless"
  project                  = var.project_id
  region                   = var.region
  network                  = google_compute_network.kb.id
  ip_cidr_range            = var.serverless_subnet_cidr
  private_ip_google_access = true
}

# Regional external managed Application Load Balancers require an ACTIVE proxy-only subnet in
# the same region. This keeps Envoy proxies and the entire IAP edge in Singapore.
resource "google_compute_subnetwork" "lb_proxy_only" {
  name          = "enterprise-knowledge-base-lb-proxy"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.kb.id
  ip_cidr_range = var.lb_proxy_subnet_cidr
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

resource "google_compute_global_address" "alloydb_psa" {
  name          = "alloydb-psa-range"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.kb.id
}

resource "google_service_networking_connection" "alloydb_psa" {
  network                 = google_compute_network.kb.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.alloydb_psa.name]
}

# ----------------------------- AlloyDB cluster ------------------------------ #
resource "google_alloydb_cluster" "kb" {
  cluster_id = "enterprise-knowledge-base"
  location   = var.region # asia-southeast1 (P-03)
  project    = var.project_id

  database_version = "POSTGRES_16"

  # PRIVATE data plane: peer the cluster into the VPC; no public surface (P-05).
  network_config {
    network = google_compute_network.kb.id
  }

  # CMEK : explicit regional key (P-09). Does not cascade from any other resource.
  encryption_config {
    kms_key_name = google_kms_crypto_key.kb.id
  }

  # Continuous backup, also CMEK-protected, kept in-region.
  continuous_backup_config {
    enabled              = true
    recovery_window_days = 14
    encryption_config {
      kms_key_name = google_kms_crypto_key.kb.id
    }
  }

  depends_on = [
    google_service_networking_connection.alloydb_psa,
    google_kms_crypto_key_iam_member.alloydb,
  ]
}

# ------------------------- AlloyDB primary instance ------------------------- #
resource "google_alloydb_instance" "primary" {
  cluster       = google_alloydb_cluster.kb.name
  instance_id   = "enterprise-knowledge-base-primary"
  instance_type = "PRIMARY"

  machine_config {
    cpu_count = 4 # ledger + pgvector chunk store; scale up as the corpus grows
  }

  # Required before ALLOYDB_IAM_USER logins can exchange ADC for database credentials.
  database_flags = {
    "alloydb.iam_authentication" = "on"
  }

  # PRIVATE only: do NOT enable Public IP. Connectivity is via the VPC/PSA above and the
  # AlloyDB connector from the app (settings ip_type=PRIVATE).
  network_config {
    enable_public_ip = false
  }

  depends_on = [google_service_networking_connection.alloydb_psa]
}

# ----------------------- Per-workload IAM DB users -------------------------- #
# The connector exchanges each workload's ADC identity for a short-lived database credential.
# These are distinct login roles: no password, Secret Manager payload, shared login or plaintext
# Terraform state. `alloydbiamuser` grants login only. Object privileges are deliberately owned
# by the schema-owner migration in infra/sql because Terraform does not connect to PostgreSQL.
locals {
  alloydb_workload_users = {
    app       = trimsuffix(google_service_account.app.email, ".gserviceaccount.com")
    pipeline  = trimsuffix(google_service_account.pipeline.email, ".gserviceaccount.com")
    migration = trimsuffix(google_service_account.migration.email, ".gserviceaccount.com")
  }
}

resource "google_alloydb_user" "workload" {
  for_each = local.alloydb_workload_users

  cluster        = google_alloydb_cluster.kb.name
  user_id        = each.value
  user_type      = "ALLOYDB_IAM_USER"
  database_roles = each.key == "migration" ? ["alloydbiamuser", "alloydbsuperuser"] : ["alloydbiamuser"]

  depends_on = [google_alloydb_instance.primary]
}

# --------------------------- The KB database -------------------------------- #
# NOTE: the google/google-beta ~> 6.0 provider line has NO google_alloydb_database
# resource. The "enterprise_kb" database (settings.yaml alloydb.database), the
# document_freshness ledger table, document_chunks pgvector table and principal_acl_tags
# binding table are therefore created by a schema-owner migration over the PRIVATE endpoint.
# `infra/sql/001_principal_acl_tags.sql` owns the ACL schema. The enterprise directory
# synchronizer owns its rows; the serving identity only queries them.
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/alloydb_instance
#
# Bootstrap is an auditable schema-owner action from inside the VPC. The protected
# schema-migration workflow runs scripts/apply_managed_schema.sh over the private endpoint with
# the Terraform-output IAM usernames and retains non-secret evidence. The application and pipeline
# identities never receive ownership or CREATE privileges.
