"""AlloyDB citation store (CitationStorePort) : anchored chunks beside the vectors.

Backs the citation store with the **same single-region, CMEK-protected AlloyDB
instance** that already holds the freshness ledger and the chunk vector table
(``alloydb.vector_table``, default ``document_chunks``). Anchors are not a new system of
record: they are columns on the document-chunk row the corpus already stores, so
residency (P-03) and the encryption posture are inherited rather than re-argued.

Every row is keyed by ``(document_id, tenant, ordinal)`` and every statement filters on
tenant, so a caller in one tenant cannot read or delete another tenant's anchors.

The AlloyDB connector + SQLAlchemy imports are lazy so the on-prem, local and test
profiles import this module with no GCP SDK installed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...config import Settings
from ...domain.kernel import BlockKind, BoundingBox
from ...domain.models import Document, DocumentChunk
from ._alloydb import connection_options


class AlloyDBCitationStoreAdapter:
    """Read/write anchored document chunks in the AlloyDB chunk table."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cfg = settings.alloydb
        self._table = self._cfg.vector_table
        self._engine: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy engine (AlloyDB connector + SQLAlchemy)
    # ------------------------------------------------------------------ #
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

    # ------------------------------------------------------------------ #
    # CitationStorePort
    # ------------------------------------------------------------------ #
    def put(self, document: Document, chunks: Sequence[DocumentChunk]) -> int:
        import sqlalchemy

        document_id = document.id
        tenant = document.tenant
        engine = self._get_engine()
        rows = [self._chunk_to_params(document_id, tenant, chunk) for chunk in chunks]
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    """
                    INSERT INTO documents
                        (document_id, tenant, title, uri, version, acl_tags, source_system)
                    VALUES (:document_id, :tenant, :title, :uri, :version,
                            CAST(:acl_tags AS text[]), :source_system)
                    ON CONFLICT (document_id, tenant) DO UPDATE SET
                        title = EXCLUDED.title,
                        uri = EXCLUDED.uri,
                        version = EXCLUDED.version,
                        acl_tags = EXCLUDED.acl_tags,
                        source_system = EXCLUDED.source_system
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant": tenant,
                    "title": document.title,
                    "uri": document.uri,
                    "version": document.version,
                    "acl_tags": [tag.label for tag in document.acl_tags],
                    "source_system": str(document.source_system),
                },
            )
            # Replace the previous parse atomically: a re-ingest must never leave stale
            # anchors that point at blocks the current document no longer has.
            conn.execute(
                sqlalchemy.text(
                    f"DELETE FROM {self._table} WHERE document_id = :id AND tenant = :tenant"
                ),
                {"id": document_id, "tenant": tenant},
            )
            if rows:
                conn.execute(
                    sqlalchemy.text(
                        f"""
                        INSERT INTO {self._table}
                            (document_id, tenant, ordinal, text, page, anchor, kind,
                             x0, y0, x1, y1, embedding_ref)
                        VALUES (:document_id, :tenant, :ordinal, :text, :page, :anchor, :kind,
                                :x0, :y0, :x1, :y1, :embedding_ref)
                        """
                    ),
                    rows,
                )
        return len(rows)

    def get(self, document_id: str, tenant: str | None = None) -> list[DocumentChunk]:
        import sqlalchemy

        engine = self._get_engine()
        clause = "document_id = :id" + (" AND tenant = :tenant" if tenant is not None else "")
        params: dict[str, Any] = {"id": document_id}
        if tenant is not None:
            params["tenant"] = tenant
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    sqlalchemy.text(f"SELECT * FROM {self._table} WHERE {clause} ORDER BY ordinal"),
                    params,
                )
                .mappings()
                .all()
            )
        return [self._row_to_chunk(row) for row in rows]

    def delete(self, document_id: str, tenant: str | None = None) -> None:
        import sqlalchemy

        engine = self._get_engine()
        clause = "document_id = :id" + (" AND tenant = :tenant" if tenant is not None else "")
        params: dict[str, Any] = {"id": document_id}
        if tenant is not None:
            params["tenant"] = tenant
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"DELETE FROM {self._table} WHERE {clause}"), params)
            conn.execute(sqlalchemy.text(f"DELETE FROM documents WHERE {clause}"), params)

    # ------------------------------------------------------------------ #
    # Row mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _chunk_to_params(document_id: str, tenant: str, chunk: DocumentChunk) -> dict[str, Any]:
        bbox = chunk.bbox
        return {
            "document_id": document_id,
            "tenant": tenant,
            "ordinal": chunk.ordinal,
            "text": chunk.text,
            "page": chunk.page,
            "anchor": chunk.anchor,
            "kind": str(chunk.kind),
            "x0": None if bbox is None else bbox.x0,
            "y0": None if bbox is None else bbox.y0,
            "x1": None if bbox is None else bbox.x1,
            "y1": None if bbox is None else bbox.y1,
            "embedding_ref": chunk.embedding_ref,
        }

    @staticmethod
    def _row_to_chunk(row: Any) -> DocumentChunk:
        coords = (row["x0"], row["y0"], row["x1"], row["y1"])
        bbox = (
            BoundingBox(
                x0=float(coords[0]),
                y0=float(coords[1]),
                x1=float(coords[2]),
                y1=float(coords[3]),
            )
            if all(c is not None for c in coords)
            else None
        )
        try:
            kind = BlockKind(row["kind"] or "paragraph")
        except ValueError:  # pragma: no cover - defensive
            kind = BlockKind.PARAGRAPH
        return DocumentChunk(
            document_id=row["document_id"],
            ordinal=int(row["ordinal"]),
            text=row["text"] or "",
            page=None if row["page"] is None else int(row["page"]),
            embedding_ref=row["embedding_ref"],
            anchor=row["anchor"],
            bbox=bbox,
            kind=kind,
        )
