# scheduler.tf : Cloud Scheduler job that refreshes the governed corpus.
#
# General Principle map:
#   P-07 (freshness / fetch-at-runtime): the governed corpus has a 7-day TTL
#         (settings.yaml corpus.ttl_days). This daily cron triggers an out-of-band
#         refresh of expiring sources so reads rarely have to re-fetch inline. It
#         invokes the dedicated Cloud Run job under the pipeline service account.
#   P-03 (residency): the scheduler and the Cloud Run job both run in asia-southeast1.
#
# The job runs daily at 02:00 Singapore time (low-traffic window). Managed serving stays
# read-only: there is no app refresh endpoint and no fallback that could bypass pipeline IAM.

# Dedicated SA the scheduler uses to authenticate to the app (OIDC).
resource "google_service_account" "scheduler" {
  account_id   = "enterprise-kb-freshness-cron"
  display_name = "A2 corpus freshness scheduler"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

# The Cloud Run job that performs the refresh (image built/pushed by CI).
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_job
resource "google_cloud_run_v2_job" "freshness_refresh" {
  name     = "enterprise-knowledge-base-freshness-refresh"
  location = var.region # asia-southeast1 (P-03)
  project  = var.project_id

  template {
    # The database advisory lease is the cross-execution safety boundary. Keep each
    # execution itself single-task as a second deploy-time invariant.
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.pipeline.email
      encryption_key  = google_kms_crypto_key.kb.id

      containers {
        # Placeholder image; CI publishes the real one to Artifact Registry in-region.
        image   = var.refresh_job_image
        command = ["python", "-m", "enterprise_kb.pipelines.refresh_job"]

        env {
          name  = "KB_PROFILE"
          value = "gcp"
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "KB_REGION"
          value = var.region
        }
        env {
          name  = "KB_ALLOYDB_URI"
          value = google_alloydb_instance.primary.name
        }
        env {
          name  = "KB_ALLOYDB_USER"
          value = google_alloydb_user.workload["pipeline"].user_id
        }
        env {
          name  = "KB_MODEL_ARMOR_TEMPLATE"
          value = google_model_armor_template.kb.template_id
        }
        env {
          name  = "KB_CORPUS_REGISTRY"
          value = var.corpus_registry_uri
        }
        env {
          name  = "KB_CORPUS_BUCKET"
          value = google_storage_bucket.corpus.name
        }
        env {
          name  = "KB_CONTROL_BUCKET"
          value = google_storage_bucket.control_inputs.name
        }
        env {
          name  = "KB_RAW_SOURCE_BUCKET"
          value = google_storage_bucket.raw_sources.name
        }
        env {
          name  = "KB_ACL_BINDINGS_URI"
          value = var.acl_bindings_uri
        }
      }

      vpc_access {
        network_interfaces {
          network    = google_compute_network.kb.name
          subnetwork = google_compute_subnetwork.serverless.name
        }
        egress = "ALL_TRAFFIC"
      }
    }
  }

  lifecycle {
    precondition {
      condition     = startswith(var.refresh_job_image, local.artifact_repo_prefix)
      error_message = "refresh_job_image must be an immutable digest from the Terraform-managed regional Artifact Registry repository."
    }
    precondition {
      condition = startswith(
        var.corpus_registry_uri,
        "gs://${google_storage_bucket.control_inputs.name}/registry/"
      )
      error_message = "corpus_registry_uri must be readable from the publisher-owned regional control bucket under registry/."
    }
    precondition {
      condition     = google_storage_bucket.raw_sources.location == var.region
      error_message = "raw source inputs must remain in the Terraform-managed regional raw-source bucket."
    }
    precondition {
      condition = startswith(
        var.acl_bindings_uri,
        "gs://${google_storage_bucket.control_inputs.name}/acl/"
      )
      error_message = "acl_bindings_uri must be readable from the publisher-owned regional control bucket under acl/."
    }
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.cloud_run,
    google_alloydb_user.workload["pipeline"],
    google_storage_bucket_iam_member.pipeline_governed_object_admin,
    google_storage_bucket_iam_member.pipeline_raw_source_viewer,
    google_storage_bucket_iam_member.pipeline_control_viewer,
    google_compute_subnetwork_iam_member.cloud_run_service_agent,
    google_logging_project_bucket_config.default,
    google_logging_project_bucket_config.required,
    terraform_data.observability_foundation,
  ]
}

# Daily cron. Invokes the Cloud Run job through the Google API with an OAuth access token.
resource "google_cloud_scheduler_job" "freshness_refresh" {
  name        = "enterprise-knowledge-base-freshness-refresh"
  description = "Daily refresh of expiring corpus documents (7-day TTL, P-07)."
  schedule    = "0 2 * * *" # 02:00 daily
  time_zone   = "Asia/Singapore"
  region      = var.region
  project     = var.project_id

  attempt_deadline = "320s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.freshness_refresh.name}:run"

    headers = {
      "Content-Type" = "application/json"
    }
    # The Cloud Run v1 jobs.run request accepts only optional `overrides`; an empty JSON
    # object starts the job with its reviewed Terraform configuration.
    body = base64encode("{}")

    # Google APIs require an OAuth access token; roles/run.invoker below supplies jobs.run.
    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job.freshness_refresh]
}

# Let the scheduler SA invoke only the Cloud Run refresh job.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoke" {
  name     = google_cloud_run_v2_job.freshness_refresh.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
