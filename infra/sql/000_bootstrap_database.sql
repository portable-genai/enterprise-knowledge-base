-- Run against the built-in `postgres` database as the dedicated migration IAM DB user.
-- psql variables: database_name, schema_owner_role, app_serving_role, pipeline_role.
-- All identifier interpolation uses format(%I), never raw substitution.
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I NOLOGIN', :'schema_owner_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'schema_owner_role')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'database_name', :'schema_owner_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'database_name')
\gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'database_name')
\gexec
SELECT format(
  'GRANT CONNECT ON DATABASE %I TO %I, %I',
  :'database_name', :'app_serving_role', :'pipeline_role'
)
\gexec
