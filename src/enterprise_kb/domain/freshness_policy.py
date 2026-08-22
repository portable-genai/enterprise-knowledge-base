"""Corpus freshness + residency policy : the fetch-and-reindex model (SPEC §2).

Pure decision logic over the freshness ledger. Documents live in the governed store;
this policy decides, from a ``FreshnessRecord``, whether a document is stale and must
be re-parsed and re-indexed before it is used to answer, and it is the home of the
residency check (P-03): a record whose ``residency_region`` is not the pinned region is
out of policy. The TTL is configurable (``CorpusSettings.ttl_days``, default 7) but
defaults are baked in so the domain can compute expiry without importing config.

No Google Cloud / framework imports : only the domain models and the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import FreshnessRecord, FreshnessStatus, utcnow
from .policy import DEFAULT_CORPUS_TTL_DAYS, DEFAULT_RESIDENCY_REGION, KbPolicy


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Compute expiry, staleness and residency-compliance for corpus documents.

    Args:
        ttl_days: freshness window in days. A document fetched more than ``ttl_days``
            ago (or not in FRESH status) is stale and must be re-indexed. Default 7.
        residency_region: the single region the corpus must stay in (P-03).
    """

    ttl_days: int = DEFAULT_CORPUS_TTL_DAYS
    residency_region: str = DEFAULT_RESIDENCY_REGION

    @staticmethod
    def from_policy(policy: KbPolicy) -> FreshnessPolicy:
        """Build the engine from the bank-owned ``policy:`` numbers (B4)."""
        return FreshnessPolicy(
            ttl_days=policy.corpus_ttl_days,
            residency_region=policy.residency_region,
        )

    def expires_at(self, fetched_at: datetime) -> datetime:
        """Return the instant at which a document fetched at ``fetched_at`` expires."""
        return fetched_at + timedelta(days=self.ttl_days)

    def is_stale(self, record: FreshnessRecord, now: datetime | None = None) -> bool:
        """Whether ``record`` must be re-indexed before use.

        A record is stale if its status is not FRESH (EXPIRED / MISSING / FAILED) or if
        the current time is at/after the TTL-derived expiry. The TTL is recomputed from
        ``fetched_at`` so the policy's own ``ttl_days`` is authoritative even if a
        stored ``expires_at`` was written under a different window.
        """
        now = now or utcnow()
        if record.status is not FreshnessStatus.FRESH:
            return True
        return now >= self.expires_at(record.fetched_at)

    def is_in_region(self, record: FreshnessRecord) -> bool:
        """Whether ``record`` is resident in the pinned region (P-03)."""
        return record.residency_region == self.residency_region

    def stale_document_ids(
        self,
        records: list[FreshnessRecord],
        now: datetime | None = None,
    ) -> list[str]:
        """Return the document_ids of every stale record, in input order."""
        now = now or utcnow()
        return [r.document_id for r in records if self.is_stale(r, now)]
