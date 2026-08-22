#!/usr/bin/env bash
set -euo pipefail

# Terraform external-data preflight for the current (2026) Observability API surface. It is
# deliberately read-only: organization/platform owners establish defaults before approving the
# full deploy, and this script verifies both those defaults and any materialized _Trace bucket.
for command_name in gcloud jq; do
  command -v "$command_name" >/dev/null || {
    echo "required observability preflight command is unavailable: ${command_name}" >&2
    exit 2
  }
done

query=$(jq -ec 'type == "object"' </dev/stdin)
project_id=$(jq -er '.project_id | select(type == "string" and length > 0)' <<<"$query")
region=$(jq -er '.region | select(type == "string" and length > 0)' <<<"$query")
jq -er '.kms_key_name | select(type == "string" and length > 0)' <<<"$query" >/dev/null

global_settings=$(gcloud beta observability settings describe \
  --project="$project_id" --location=global --format=json --quiet)
regional_settings=$(gcloud beta observability settings describe \
  --project="$project_id" --location="$region" --format=json --quiet)
buckets=$(gcloud beta observability buckets list \
  --project="$project_id" --location=- --format=json --quiet)

default_storage_location=$(jq -r '.defaultStorageLocation // ""' <<<"$global_settings")
default_kms_key=$(jq -r '.kmsKeyName // ""' <<<"$regional_settings")
trace_buckets=$(jq -c '
  [(if type == "array" then .[] else (.buckets // [])[] end)
   | select(.name | endswith("/buckets/_Trace"))]
' <<<"$buckets")
trace_bucket_count=$(jq -r 'length' <<<"$trace_buckets")

if ((trace_bucket_count > 1)); then
  echo "multiple _Trace buckets exist; trace residency is ambiguous" >&2
  exit 3
fi

trace_bucket_location=""
trace_bucket_kms_key=""
if ((trace_bucket_count == 1)); then
  trace_bucket_location=$(jq -er '.[0].name | capture("/locations/(?<location>[^/]+)/").location' <<<"$trace_buckets")
  trace_bucket_kms_key=$(jq -r '.[0].cmekSettings.kmsKey // ""' <<<"$trace_buckets")
fi

jq -cn \
  --arg default_storage_location "$default_storage_location" \
  --arg default_kms_key "$default_kms_key" \
  --arg trace_bucket_count "$trace_bucket_count" \
  --arg trace_bucket_location "$trace_bucket_location" \
  --arg trace_bucket_kms_key "$trace_bucket_kms_key" \
  '{default_storage_location: $default_storage_location,
    default_kms_key: $default_kms_key,
    trace_bucket_count: $trace_bucket_count,
    trace_bucket_location: $trace_bucket_location,
    trace_bucket_kms_key: $trace_bucket_kms_key}'
