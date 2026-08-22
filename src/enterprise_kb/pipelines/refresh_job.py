"""Entrypoint for the scheduled corpus freshness refresh job.

This is the out-of-band half of the fetch-and-reindex freshness model (SPEC §2). It is
deployed as a **Cloud Run job** (see ``pipelines/Dockerfile``) and triggered on a schedule
(Cloud Scheduler) more often than the TTL : e.g. daily for a 7-day TTL : so that by the
time a read references a document it is almost always already fresh in the governed store,
with its freshness recorded in the ledger.

Default behaviour refreshes only documents that are expired in the ledger or not yet
ingested (:func:`ingest.refresh_expired`). ``--full`` forces a re-ingest of the entire
registry (:func:`ingest.refresh_all`). The job is idempotent: re-running it on a fresh
corpus is a cheap series of ledger reads.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..config import Container, Settings
from ..managed_preflight import assert_managed_pipeline_ready
from . import ingest
from .acl_sync import sync_acl_bindings

logger = logging.getLogger("enterprise_kb.pipelines.refresh_job")

# One database-scoped lease serializes ACL replacement and every corpus mutation. A
# PostgreSQL advisory lock is session-owned, automatically released if the worker dies,
# and shared by every Cloud Run job execution connected to this AlloyDB database.
_MANAGED_REFRESH_LOCK_ID = 2_158_656_292_000_002


@contextmanager
def _managed_refresh_lease(container: Any) -> Iterator[None]:
    """Acquire the one managed refresh lease, or refuse a concurrent execution."""
    if container.settings.profile not in {"gcp", "platform"}:
        yield
        return

    engine = container.access_control._get_engine()
    with engine.connect() as connection:
        acquired = bool(
            connection.exec_driver_sql(
                "SELECT pg_try_advisory_lock(%s)",
                (_MANAGED_REFRESH_LOCK_ID,),
            ).scalar()
        )
        if not acquired:
            raise RuntimeError(
                "another governed corpus refresh owns the AlloyDB lease; refusing "
                "overlapping ACL/index/ledger mutations"
            )
        try:
            yield
        finally:
            connection.exec_driver_sql(
                "SELECT pg_advisory_unlock(%s)",
                (_MANAGED_REFRESH_LOCK_ID,),
            )


def run(*, full: bool = False, settings: Settings | None = None) -> ingest.RefreshSummary:
    """Build the Container from settings and run one refresh pass."""
    chosen = settings or Settings.load()
    assert_managed_pipeline_ready(chosen)
    container = Container(chosen)
    with _managed_refresh_lease(container):
        # Fetch the reviewed ACL and registry artifacts only after the lease is held.
        # A waiting/older execution cannot preload stale authority and later overwrite
        # a newer projection.
        synced = sync_acl_bindings(container)
        mode = "full" if full else "expired-only"
        logger.info(
            "corpus refresh starting: mode=%s profile=%s region=%s ttl_days=%d",
            mode,
            container.settings.profile,
            container.settings.region,
            container.settings.corpus.ttl_days,
        )
        logger.info("ACL binding projection synchronized: rows=%d", synced)

        summary = ingest.refresh_all(container) if full else ingest.refresh_expired(container)

    logger.info(
        "corpus refresh complete: total=%d ingested=%d skipped=%d failed=%d",
        summary.total,
        summary.ingested,
        summary.skipped,
        summary.failed,
    )
    for outcome in summary.outcomes:
        if outcome.action == "failed":
            logger.error("  FAILED %s: %s", outcome.document_id, outcome.detail)
        else:
            logger.info(
                "  %-14s %s (chunks=%d)", outcome.action, outcome.document_id, outcome.chunks
            )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enterprise-knowledge-base-refresh-job",
        description=(
            "Refresh the A2 corpus: re-fetch expired documents into the governed store "
            "and update the freshness/residency ledger."
        ),
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-ingest every document in the registry, not just expired/missing ones.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="logging level (DEBUG, INFO, WARNING, ERROR). Defaults to INFO.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI / container entrypoint. Returns a non-zero exit code if any document failed."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    summary = run(full=bool(args.full))
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
