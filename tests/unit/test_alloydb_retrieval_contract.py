"""Static managed retrieval contract: scope and freshness precede rank/limit."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_alloydb_retrieval_is_tenant_acl_and_freshness_scoped_before_limit() -> None:
    source = (ROOT / "src/enterprise_kb/adapters/gcp/alloydb_retrieval.py").read_text(
        encoding="utf-8"
    )
    assert "c.tenant = '' OR c.tenant = :tenant" in source
    assert "d.acl_tags <@ CAST(:allowed_tags AS text[])" in source
    assert "cardinality(d.acl_tags) > 0" in source
    assert "f.status = 'fresh'" in source
    assert "f.expires_at > CURRENT_TIMESTAMP" in source
    assert source.index("f.expires_at > CURRENT_TIMESTAMP") < source.index("LIMIT :top_k")


def test_removed_registry_sources_are_tombstoned_not_left_live() -> None:
    pipeline = (ROOT / "src/enterprise_kb/pipelines/ingest.py").read_text(encoding="utf-8")
    assert "source removed from reviewed registry; indexed copy tombstoned" in pipeline
    assert "service.delete(record.document_id" in pipeline
