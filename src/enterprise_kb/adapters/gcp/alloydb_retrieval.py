"""Singapore-resident managed retrieval over AlloyDB PostgreSQL full-text search.

All consequential scope is pushed into SQL using the verified tenant, the already-resolved
ACL tags and the freshness ledger. The domain repeats tenant/all-of ACL admission after this
candidate stage. PostgreSQL FTS is intentionally portable: an exit deployment can run the
same query contract outside GCP, while AlloyDB supplies managed HA, IAM auth and performance.
"""

from __future__ import annotations

import re
from typing import Any

from ...config import Settings
from ...domain.kernel import BoundingBox
from ...domain.models import AclTag, Citation, KbQuery, RetrievedPassage
from ._alloydb import connection_options

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


class AlloyDBRetrievalAdapter:
    """Retrieve only fresh, tenant-visible, ACL-admissible PostgreSQL FTS candidates."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cfg = settings.alloydb
        self._table = self._cfg.vector_table
        if _IDENTIFIER.fullmatch(self._table) is None:
            raise ValueError("alloydb.vector_table must be a simple PostgreSQL identifier")
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        options = connection_options(self._cfg)
        import sqlalchemy
        from google.cloud.alloydb.connector import Connector, IPTypes

        connector = Connector()

        def _connect() -> Any:
            return connector.connect(
                self._cfg.instance_uri,
                "pg8000",
                user=options["user"],
                db=options["db"],
                ip_type=IPTypes(options["ip_type"]),
                enable_iam_auth=options["enable_iam_auth"],
            )

        self._engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=_connect)
        return self._engine

    def retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        """Apply text rank only after tenant, ACL and not-expired freshness scope."""
        if not query.allowed_tags:
            return []
        unsupported = sorted(set(query.filters) - {"source_system"})
        if unsupported:
            raise ValueError(f"unsupported AlloyDB retrieval filters: {', '.join(unsupported)}")

        import sqlalchemy

        text_clause = ""
        rank_expr = "0.0"
        if query.text.strip():
            text_clause = "AND c.search_vector @@ websearch_to_tsquery('simple', :query_text)"
            rank_expr = "ts_rank_cd(c.search_vector, websearch_to_tsquery('simple', :query_text))"
        source_clause = ""
        if query.filters.get("source_system"):
            source_clause = "AND d.source_system = :source_system"

        statement = sqlalchemy.text(
            f"""
            SELECT c.document_id, c.tenant, c.text, c.page, c.anchor, c.kind,
                   c.x0, c.y0, c.x1, c.y1,
                   d.title, d.uri, d.version, d.acl_tags,
                   {rank_expr} AS score
              FROM {self._table} AS c
              JOIN documents AS d
                ON d.document_id = c.document_id AND d.tenant = c.tenant
              JOIN document_freshness AS f
                ON f.document_id = c.document_id AND f.tenant = c.tenant
             WHERE (c.tenant = '' OR c.tenant = :tenant)
               AND d.acl_tags <@ CAST(:allowed_tags AS text[])
               AND cardinality(d.acl_tags) > 0
               AND f.status = 'fresh'
               AND f.expires_at > CURRENT_TIMESTAMP
               {text_clause}
               {source_clause}
             ORDER BY score DESC, c.document_id, c.ordinal
             LIMIT :top_k
            """
        )
        params: dict[str, Any] = {
            "tenant": query.tenant,
            "allowed_tags": list(query.allowed_tags),
            "query_text": query.text,
            "top_k": max(query.top_k, 1),
            "source_system": query.filters.get("source_system", ""),
        }
        with self._get_engine().connect() as conn:
            rows = conn.execute(statement, params).mappings().all()
        return [self._row_to_passage(row) for row in rows]

    @staticmethod
    def _row_to_passage(row: Any) -> RetrievedPassage:
        coords = (row["x0"], row["y0"], row["x1"], row["y1"])
        bbox = None
        if all(value is not None for value in coords):
            bbox = BoundingBox(
                x0=float(coords[0]),
                y0=float(coords[1]),
                x1=float(coords[2]),
                y1=float(coords[3]),
            )
        score = float(row["score"] or 0.0)
        citation = Citation(
            document_id=str(row["document_id"]),
            title=str(row["title"]),
            uri=str(row["uri"]),
            version=str(row["version"] or "unknown"),
            page=None if row["page"] is None else int(row["page"]),
            snippet=str(row["text"] or "")[:280],
            score=score,
            anchor=row["anchor"],
            bbox=bbox,
        )
        return RetrievedPassage(
            text=str(row["text"] or ""),
            citation=citation,
            score=score,
            acl_tags=tuple(AclTag(label=str(tag)) for tag in (row["acl_tags"] or ())),
            tenant=str(row["tenant"] or ""),
        )
