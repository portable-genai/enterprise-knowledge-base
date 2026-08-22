# AlloyDB schema and role migration

Terraform creates the private AlloyDB cluster and three passwordless IAM login users (app,
pipeline and migration), but the
Google provider does not connect to PostgreSQL and therefore cannot own database objects or grant
table privileges. A reviewed schema-owner job must run these migrations through the private
AlloyDB connector. It uses an independently governed migration identity, never an application
service account and never a password stored in Terraform.

Read the exact usernames without treating them as secrets:

```sh
terraform -chdir=infra/terraform output -json alloydb_iam_database_users
```

Pass each output identity to its explicit migration role:

| Terraform output key | Migration variable | ACL-table grant |
| --- | --- | --- |
| `app` | `app_serving_role` | `USAGE`, `SELECT` |
| `pipeline` | `pipeline_role` | `USAGE`, `SELECT`, `INSERT`, `UPDATE`, `DELETE` |

Run the exact audited bootstrap/migration workflow:

```sh
scripts/apply_managed_schema.sh
```

`000` creates the `enterprise_kb` database and non-login owner role against `postgres`; `001`
creates ACL, document, searchable chunk and freshness tables. The serving IAM user receives only
schema `USAGE` and table `SELECT`. The
pipeline IAM user receives the DML needed to synchronize bindings. `PUBLIC` receives neither
schema creation nor table privileges. Record the migration digest and privilege-query output in
the deployment evidence bundle before enabling managed serving.
