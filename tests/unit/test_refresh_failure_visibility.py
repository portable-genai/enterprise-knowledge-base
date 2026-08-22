from types import SimpleNamespace

from enterprise_kb.domain.models import Document, FreshnessRecord, FreshnessStatus, utcnow
from enterprise_kb.pipelines import ingest


def test_refresh_hides_old_projection_before_fetch_can_crash(monkeypatch) -> None:
    writes = []
    ledger = SimpleNamespace(upsert=writes.append)
    container = SimpleNamespace(
        ledger=ledger,
        settings=SimpleNamespace(region="asia-southeast1"),
    )

    def crash_after_asserting_hidden(document):  # noqa: ANN001
        assert document.id == "policy"
        assert writes and writes[0].status is FreshnessStatus.FAILED
        raise RuntimeError("worker terminated during fetch")

    monkeypatch.setattr(ingest.fetch_mod, "fetch_document", crash_after_asserting_hidden)
    outcome = ingest.ingest_document(
        container, Document(id="policy", title="Policy", uri="gs://raw/sources/policy.pdf")
    )

    assert outcome.status is FreshnessStatus.FAILED
    assert all(record.expires_at <= record.fetched_at for record in writes)
    assert all(record.source_authority == "registry" for record in writes)


def test_registry_refresh_never_deletes_direct_api_owned_document(monkeypatch) -> None:
    now = utcnow()
    direct = FreshnessRecord(
        document_id="api-upload",
        residency_region="asia-southeast1",
        fetched_at=now,
        expires_at=now,
        source_authority="direct",
    )
    ledger = SimpleNamespace(
        all=lambda: [direct],
        list_expired=lambda _now=None: [direct],
    )
    container = SimpleNamespace(ledger=ledger)
    deleted: list[str] = []
    monkeypatch.setattr(ingest, "load_documents", lambda _container: [])
    monkeypatch.setattr(
        ingest,
        "_service",
        lambda _container: SimpleNamespace(
            delete=lambda document_id, actor, tenant: deleted.append(document_id)
        ),
    )

    summary = ingest.refresh_expired(container, now=now)

    assert deleted == []
    assert summary.outcomes == ()


def test_registry_refresh_tombstones_only_registry_owned_document(monkeypatch) -> None:
    now = utcnow()
    governed = FreshnessRecord(
        document_id="withdrawn-policy",
        residency_region="asia-southeast1",
        fetched_at=now,
        expires_at=now,
        source_authority="registry",
    )
    ledger = SimpleNamespace(
        all=lambda: [governed],
        list_expired=lambda _now=None: [governed],
    )
    container = SimpleNamespace(ledger=ledger)
    deleted: list[str] = []
    monkeypatch.setattr(ingest, "load_documents", lambda _container: [])
    monkeypatch.setattr(
        ingest,
        "_service",
        lambda _container: SimpleNamespace(
            delete=lambda document_id, actor, tenant: deleted.append(document_id)
        ),
    )

    summary = ingest.refresh_expired(container, now=now)

    assert deleted == ["withdrawn-policy"]
    assert summary.outcomes[0].action == "deleted"
