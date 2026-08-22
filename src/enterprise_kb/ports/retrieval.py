"""RetrievalPort and AccessControlPort : ACL-aware retrieval over the corpus.

Primary GCP adapter: private **AlloyDB PostgreSQL full-text search**
on the Gemini Enterprise Agent Platform, pinned to a single in-country region. The
access-control port resolves a caller's principals into the ACL tags it may see; the
ACL-filtering *decision* stays in the domain (P-09), this port only resolves principal
to tags. On-prem migration swaps both for placeholder adapters with no change to
callers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import KbQuery, RetrievedPassage


@runtime_checkable
class RetrievalPort(Protocol):
    def retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        """Return ranked passages (each ACL-tagged and cited) for ``query``.

        The adapter may pre-filter by the query's server-resolved ``allowed_tags`` and verified
        ``tenant``, but the domain re-filters both, so the access decision is never delegated.
        """
        ...


@runtime_checkable
class AccessControlPort(Protocol):
    def resolve(self, principals: list[str], tenant: str) -> set[str]:
        """Expand a list of principal ids into the set of ACL tag labels they may see.

        ``principals`` and ``tenant`` come from the server-verified identity. The managed
        adapter must enforce the tenant in its lookup rather than treating it as an
        after-the-fact retrieval filter. Returns an empty set when the principals grant no
        visibility; the managed adapter also denies an absent tenant.
        """
        ...
