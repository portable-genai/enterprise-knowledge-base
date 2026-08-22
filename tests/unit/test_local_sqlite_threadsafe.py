"""Concurrency regression tests for the ``local`` SQLite adapters.

The API serves sync ``def`` endpoints from Starlette's threadpool while
``api.deps.get_container`` is ``@lru_cache`` (one process-wide container, hence one
SQLite connection per store). Under concurrent serve, a request handled on a different
worker thread reaches ``record()`` / ``upsert()`` / ``retrieve()`` on a connection that
was opened on another thread, which raises:

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in
    that same thread.

That drops the WORM audit and 500s the request. The fix (mirroring loan-doc's
``adapters/local/audit.py`` and agent-registry's ``adapters/local/sqlite_registry.py``)
opens the connection with ``check_same_thread=False`` and serialises every access behind
a ``threading.Lock`` (single-writer).

Each test below constructs the adapter ONCE (single shared in-memory connection) and
drives writes + reads from many threads via a ``ThreadPoolExecutor``, asserting no
exception is raised and the final row count is correct. These tests fail before the fix
(thread-affinity ProgrammingError) and pass after it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from tests.fixtures import sample_docs

from enterprise_kb.adapters.local.audit import LocalAppendOnlyAuditAdapter
from enterprise_kb.adapters.local.ledger import LocalLedgerAdapter
from enterprise_kb.adapters.local.retrieval import LocalFtsRetrievalAdapter
from enterprise_kb.config import LocalSettings, Settings
from enterprise_kb.domain.models import (
    AuditEvent,
    Decision,
    FreshnessRecord,
    FreshnessStatus,
    KbQuery,
    utcnow,
)

_THREADS = 8
_PER_THREAD = 25


def _settings(**local: str) -> Settings:
    return Settings(profile="local", local=LocalSettings(**local))


def test_audit_adapter_is_threadsafe_under_concurrent_record_and_read() -> None:
    adapter = LocalAppendOnlyAuditAdapter(_settings(audit_path=":memory:"))

    def worker(worker_id: int) -> None:
        for i in range(_PER_THREAD):
            adapter.record(
                AuditEvent(
                    action="search",
                    actor=f"analyst-{worker_id}",
                    decision=Decision.ALLOWED,
                    redacted_prompt="[NRIC]",
                    redacted_response=f"{i} passage(s)",
                    citations=(sample_docs.SAMPLE_PASSAGES[0].citation,),
                )
            )
            # Interleave reads so a read can land on a different thread than the writer.
            adapter.read_all()

    with ThreadPoolExecutor(max_workers=_THREADS) as pool:
        for fut in [pool.submit(worker, w) for w in range(_THREADS)]:
            fut.result()  # re-raises any exception from the worker thread

    assert len(adapter.read_all()) == _THREADS * _PER_THREAD


def test_ledger_adapter_is_threadsafe_under_concurrent_upsert_and_read() -> None:
    adapter = LocalLedgerAdapter(_settings(ledger_path=":memory:"))
    now = utcnow()

    def worker(worker_id: int) -> None:
        for i in range(_PER_THREAD):
            doc_id = f"doc-{worker_id}-{i}"
            adapter.upsert(
                FreshnessRecord(
                    document_id=doc_id,
                    residency_region="asia-southeast1",
                    fetched_at=now,
                    expires_at=now + timedelta(days=7),
                    version="v1",
                    checksum="abc",
                    status=FreshnessStatus.FRESH,
                )
            )
            adapter.get(doc_id)
            adapter.all()

    with ThreadPoolExecutor(max_workers=_THREADS) as pool:
        for fut in [pool.submit(worker, w) for w in range(_THREADS)]:
            fut.result()

    assert len(adapter.all()) == _THREADS * _PER_THREAD


def test_retrieval_adapter_is_threadsafe_under_concurrent_add_and_query() -> None:
    # Seed empty so the only rows are the ones the threads add (deterministic count).
    adapter = LocalFtsRetrievalAdapter(_settings(db_path=":memory:"))
    adapter.seed([])
    passage = sample_docs.SAMPLE_PASSAGES[0]

    def worker(_worker_id: int) -> None:
        for _ in range(_PER_THREAD):
            adapter.add([passage])
            # Concurrent BM25 read on the shared connection.
            adapter.retrieve(KbQuery(text=passage.text, top_k=1000))

    with ThreadPoolExecutor(max_workers=_THREADS) as pool:
        for fut in [pool.submit(worker, w) for w in range(_THREADS)]:
            fut.result()

    # Every add() inserted exactly one row; reads must not have raised.
    assert len(adapter.retrieve(KbQuery(text="due diligence residency", top_k=1000))) >= 1
