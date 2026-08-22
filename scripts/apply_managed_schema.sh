#!/usr/bin/env bash
set -euo pipefail

# Audited, passwordless AlloyDB bootstrap. Run from a VPC-connected host under the dedicated
# migration service account. No password or Terraform state value is read or emitted.
required=(
  KB_ALLOYDB_URI KB_MIGRATION_IAM_DB_USER KB_APP_IAM_DB_USER
  KB_PIPELINE_IAM_DB_USER KB_SCHEMA_EVIDENCE_DIR
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "required environment setting is absent or empty: ${name}" >&2
    exit 2
  fi
done
for command_name in alloydb-auth-proxy psql sha256sum; do
  command -v "$command_name" >/dev/null || {
    echo "required command is unavailable: ${command_name}" >&2
    exit 2
  }
done

umask 077
mkdir -p "$KB_SCHEMA_EVIDENCE_DIR"
test ! -e "$KB_SCHEMA_EVIDENCE_DIR/complete"

proxy_log="$KB_SCHEMA_EVIDENCE_DIR/proxy.log"
alloydb-auth-proxy "$KB_ALLOYDB_URI" --auto-iam-authn --address 127.0.0.1 --port 5433 \
  >"$proxy_log" 2>&1 &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null || true' EXIT

for attempt in {1..30}; do
  if psql "host=127.0.0.1 port=5433 dbname=postgres user=${KB_MIGRATION_IAM_DB_USER} sslmode=disable" \
    -v ON_ERROR_STOP=1 -Atc 'SELECT current_user' >"$KB_SCHEMA_EVIDENCE_DIR/preflight.txt" 2>&1; then
    break
  fi
  if [[ "$attempt" == 30 ]]; then
    echo "AlloyDB IAM preflight failed" >&2
    exit 3
  fi
  sleep 1
done

common_vars=(
  -v database_name=enterprise_kb
  -v schema_owner_role=enterprise_kb_schema_owner
  -v app_serving_role="$KB_APP_IAM_DB_USER"
  -v pipeline_role="$KB_PIPELINE_IAM_DB_USER"
)
psql "host=127.0.0.1 port=5433 dbname=postgres user=${KB_MIGRATION_IAM_DB_USER} sslmode=disable" \
  -v ON_ERROR_STOP=1 "${common_vars[@]}" -f infra/sql/000_bootstrap_database.sql
psql "host=127.0.0.1 port=5433 dbname=enterprise_kb user=${KB_MIGRATION_IAM_DB_USER} sslmode=disable" \
  -v ON_ERROR_STOP=1 "${common_vars[@]}" -f infra/sql/001_principal_acl_tags.sql

sha256sum infra/sql/000_bootstrap_database.sql infra/sql/001_principal_acl_tags.sql \
  >"$KB_SCHEMA_EVIDENCE_DIR/migration-sha256.txt"
psql "host=127.0.0.1 port=5433 dbname=enterprise_kb user=${KB_MIGRATION_IAM_DB_USER} sslmode=disable" \
  -v ON_ERROR_STOP=1 -P pager=off -c \
  "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants WHERE table_schema='public' ORDER BY grantee, table_name, privilege_type" \
  >"$KB_SCHEMA_EVIDENCE_DIR/table-grants.txt"
date -u +%Y-%m-%dT%H:%M:%SZ >"$KB_SCHEMA_EVIDENCE_DIR/complete"
echo "schema migration complete; evidence: $KB_SCHEMA_EVIDENCE_DIR"
