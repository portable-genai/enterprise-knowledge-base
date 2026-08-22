"""The citation store (slice 3): round-trip, re-ingest, and cross-tenant denial.

The cross-tenant test is the important one. Anchors are read on the answer path with a
document id the caller supplies indirectly (via what retrieval returned), so an
object-level authorization miss here would let a caller in one tenant pull another
tenant's document text back as "evidence". The store gates on the tenant the domain
passes down from the server-verified principal, and returns nothing (never another
tenant's blocks) when they do not match.
"""

from __future__ import annotations

import pytest

from enterprise_kb.adapters.local.citation_store import LocalSqliteCitationStore
from enterprise_kb.adapters.onprem.citation_store import OnPremCitationStoreAdapter
from enterprise_kb.config import LocalSettings, Settings
from enterprise_kb.domain.kernel import BlockKind, BoundingBox
from enterprise_kb.domain.models import Document, DocumentChunk
from enterprise_kb.ports import CitationStorePort


def _settings() -> Settings:
    return Settings(profile="local", local=LocalSettings(db_path=":memory:"))


def _chunks(document_id: str = "doc", n: int = 3) -> list[DocumentChunk]:
    return [
        DocumentChunk(
            document_id=document_id,
            ordinal=i,
            text=f"block {i} of {document_id}",
            page=1 + i // 2,
            anchor=f"p{1 + i // 2}#b{i % 2}",
            bbox=BoundingBox(x0=0.0, y0=0.1 * i, x1=0.5, y1=0.1 * i + 0.05),
            kind=BlockKind.TABLE if i == 2 else BlockKind.PARAGRAPH,
        )
        for i in range(n)
    ]


def _document(document_id: str = "doc", tenant: str = "") -> Document:
    return Document(id=document_id, title=document_id, uri="", tenant=tenant)


@pytest.fixture
def store() -> LocalSqliteCitationStore:
    return LocalSqliteCitationStore(_settings())


def test_local_store_satisfies_the_port(store: LocalSqliteCitationStore) -> None:
    assert isinstance(store, CitationStorePort)


def test_roundtrip_preserves_anchor_box_and_kind(store: LocalSqliteCitationStore) -> None:
    written = _chunks()
    assert store.put(_document(tenant="tenant-a"), written) == 3
    read = store.get("doc", "tenant-a")
    assert read == written, "chunks must round-trip byte-for-byte, in ordinal order"


def test_put_replaces_the_previous_parse(store: LocalSqliteCitationStore) -> None:
    store.put(_document(tenant="tenant-a"), _chunks(n=3))
    store.put(_document(tenant="tenant-a"), _chunks(n=1))
    read = store.get("doc", "tenant-a")
    assert [c.ordinal for c in read] == [0], "a re-ingest must not leave stale anchors behind"


def test_cross_tenant_read_is_denied(store: LocalSqliteCitationStore) -> None:
    """Tenant B must never see tenant A's anchors, even knowing the document id."""
    store.put(_document(tenant="tenant-a"), _chunks())
    assert store.get("doc", "tenant-a"), "the owning tenant still reads its own anchors"
    assert store.get("doc", "tenant-b") == [], "cross-tenant read must return nothing"


def test_cross_tenant_delete_is_denied(store: LocalSqliteCitationStore) -> None:
    store.put(_document(tenant="tenant-a"), _chunks())
    store.delete("doc", "tenant-b")
    assert store.get("doc", "tenant-a"), "another tenant's delete must not remove these rows"
    store.delete("doc", "tenant-a")
    assert store.get("doc", "tenant-a") == []


def test_same_document_id_in_two_tenants_stays_separate(store: LocalSqliteCitationStore) -> None:
    store.put(_document(tenant="tenant-a"), _chunks(document_id="doc", n=2))
    store.put(_document(tenant="tenant-b"), _chunks(document_id="doc", n=3))
    assert len(store.get("doc", "tenant-a")) == 2
    assert len(store.get("doc", "tenant-b")) == 3


def test_unscoped_read_is_trusted_local_tooling(store: LocalSqliteCitationStore) -> None:
    """An omitted/None tenant is the explicit offline CLI / eval convention."""
    store.put(_document(), _chunks(n=2))
    assert len(store.get("doc")) == 2


def test_exact_global_tenant_does_not_read_colliding_owned_chunks(
    store: LocalSqliteCitationStore,
) -> None:
    global_chunks = _chunks(n=1)
    owned_chunks = _chunks(n=2)
    store.put(_document(tenant=""), global_chunks)
    store.put(_document(tenant="tenant-a"), owned_chunks)

    assert store.get("doc", "") == global_chunks
    assert len(store.get("doc", None)) == 3


def test_unknown_document_returns_empty(store: LocalSqliteCitationStore) -> None:
    assert store.get("never-ingested", "tenant-a") == []


def test_chunk_without_a_box_roundtrips_as_unanchored(store: LocalSqliteCitationStore) -> None:
    chunk = DocumentChunk(document_id="doc", ordinal=0, text="legacy", page=7)
    store.put(_document(), [chunk])
    assert store.get("doc") == [chunk]


# --------------------------------------------------------------------------- #
# The on-prem family: fail-fast placeholder, the reversibility proof
# --------------------------------------------------------------------------- #
def test_onprem_store_satisfies_the_port_and_fails_fast() -> None:
    adapter = OnPremCitationStoreAdapter(_settings())
    assert isinstance(adapter, CitationStorePort)
    with pytest.raises(NotImplementedError):
        adapter.get("doc", "tenant-a")
    with pytest.raises(NotImplementedError):
        adapter.put(_document(tenant="tenant-a"), [])
    with pytest.raises(NotImplementedError):
        adapter.delete("doc", "tenant-a")
