-- Run on enterprise_kb as the dedicated migration IAM DB user after 000 bootstrap.
-- Required psql variables (pass each with -v, using Terraform output values):
--   schema_owner_role  non-login owner of application objects
--   app_serving_role   IAM DB user for the API (SELECT only)
--   pipeline_role      IAM DB user for ingestion and enterprise-directory sync (read/write)
--
-- Identifiers are rendered with psql's :"name" identifier quoting. The migration owner must
-- create the database and non-login schema-owner role out of band. Terraform provisions the three
-- distinct IAM login users but cannot connect to PostgreSQL to grant object privileges. A reviewed
-- migration runner applies this file over the private connector and records its digest.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE TABLE IF NOT EXISTS principal_acl_tags (
    tenant       TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    tag_label    TEXT NOT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant, principal_id, tag_label)
);

CREATE INDEX IF NOT EXISTS principal_acl_tags_lookup
    ON principal_acl_tags (tenant, principal_id)
    WHERE enabled IS TRUE;

CREATE TABLE IF NOT EXISTS documents (
    document_id   TEXT NOT NULL,
    tenant        TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL,
    uri           TEXT NOT NULL,
    version       TEXT NOT NULL DEFAULT 'unknown',
    acl_tags      TEXT[] NOT NULL DEFAULT '{}',
    source_system TEXT NOT NULL DEFAULT 'other',
    PRIMARY KEY (document_id, tenant)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    document_id   TEXT NOT NULL,
    tenant        TEXT NOT NULL DEFAULT '',
    ordinal       INTEGER NOT NULL,
    text          TEXT NOT NULL DEFAULT '',
    page          INTEGER,
    anchor        TEXT,
    kind          TEXT NOT NULL DEFAULT 'paragraph',
    x0            DOUBLE PRECISION,
    y0            DOUBLE PRECISION,
    x1            DOUBLE PRECISION,
    y1            DOUBLE PRECISION,
    embedding_ref TEXT,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    PRIMARY KEY (document_id, tenant, ordinal)
);

CREATE INDEX IF NOT EXISTS document_chunks_search
    ON document_chunks USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS document_freshness (
    document_id      TEXT NOT NULL,
    tenant           TEXT NOT NULL DEFAULT '',
    residency_region TEXT NOT NULL,
    fetched_at       TIMESTAMPTZ NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL,
    version          TEXT NOT NULL DEFAULT 'unknown',
    checksum         TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'fresh',
    source_authority TEXT NOT NULL DEFAULT 'direct',
    PRIMARY KEY (document_id, tenant)
);

-- Preserve forward compatibility when this migration is applied to an existing database
-- created by an earlier release. The default deliberately classifies legacy/API-owned rows as
-- direct so a registry reconciliation cannot delete them merely because provenance was absent.
ALTER TABLE document_freshness
    ADD COLUMN IF NOT EXISTS source_authority TEXT NOT NULL DEFAULT 'direct';

ALTER TABLE principal_acl_tags OWNER TO :"schema_owner_role";
ALTER TABLE documents OWNER TO :"schema_owner_role";
ALTER TABLE document_chunks OWNER TO :"schema_owner_role";
ALTER TABLE document_freshness OWNER TO :"schema_owner_role";

REVOKE ALL ON TABLE principal_acl_tags, documents, document_chunks, document_freshness FROM PUBLIC;
REVOKE ALL ON TABLE principal_acl_tags, documents, document_chunks, document_freshness FROM :"app_serving_role";
GRANT USAGE ON SCHEMA public TO :"app_serving_role";
GRANT SELECT ON TABLE principal_acl_tags, documents, document_chunks, document_freshness TO :"app_serving_role";

REVOKE ALL ON TABLE principal_acl_tags, documents, document_chunks, document_freshness FROM :"pipeline_role";
GRANT USAGE ON SCHEMA public TO :"pipeline_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE principal_acl_tags, documents, document_chunks, document_freshness TO :"pipeline_role";
