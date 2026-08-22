"""Local citation store (CitationStorePort) : anchored chunks in the local SQLite file.

The ``local`` profile's stand-in for the AlloyDB chunk table. It writes to the SAME
SQLite database the FTS5 retrieval index uses (``settings.local.db_path``), in a plain
``document_chunks`` table beside the ``passages`` virtual table, so an offline ingest
leaves both the searchable passages and the anchors a claim can be resolved against in
one file, and a demo needs no second store.

SDK-free and deterministic. Rows come back in ``ordinal`` order, which is the reading
order the parser emitted, so resolution is reproducible.

Tenancy is enforced here in SQL and is fail-closed: a supplied ``tenant`` (including the
empty shared/global tenant) matches only that tenant's rows. ``None`` is the explicit
trusted local-tooling unscoped mode.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path

from ...config import Settings
from ...domain.kernel import BlockKind, BoundingBox
from ...domain.models import Document, DocumentChunk
from ._seed import SEED_CHUNK_TENANTS, SEED_CHUNKS

_DEFAULT_DB_DIR = Path.home() / ".enterprise_kb"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "local.db"

_DDL = """
CREATE TABLE IF NOT EXISTS document_chunks (
    document_id   TEXT NOT NULL,
    tenant        TEXT NOT NULL DEFAULT '',
    ordinal       INTEGER NOT NULL,
    text          TEXT NOT NULL DEFAULT '',
    page          INTEGER,
    anchor        TEXT,
    kind          TEXT NOT NULL DEFAULT 'paragraph',
    x0            REAL,
    y0            REAL,
    x1            REAL,
    y1            REAL,
    embedding_ref TEXT,
    PRIMARY KEY (document_id, tenant, ordinal)
)
"""


class LocalSqliteCitationStore:
    """Store and read anchored document chunks in the local SQLite database."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_path = getattr(getattr(settings, "local", None), "db_path", "") or str(_DEFAULT_DB_PATH)
        self._db_path = db_path
        # Same single-connection + lock discipline as the local retrieval adapter: the
        # container is cached per process and reached from Starlette worker threads.
        self._lock = threading.Lock()
        self._conn = self._connect(db_path)
        with self._lock:
            self._conn.execute(_DDL)
            self._conn.commit()
        # Self-seed the built-in corpus's anchors, mirroring the retrieval adapter, so an
        # out-of-the-box offline run resolves claims to a block with no ingest step.
        if self._is_empty():
            self._seed()

    @staticmethod
    def _connect(db_path: str) -> sqlite3.Connection:
        if db_path not in (":memory:", "") and not db_path.startswith("file:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_empty(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT count(*) AS n FROM document_chunks").fetchone()
        return int(row["n"]) == 0

    def _seed(self) -> None:
        """Load the built-in seed chunks, each under its own tenant partition."""
        by_key: dict[tuple[str, str], list[DocumentChunk]] = {}
        for chunk in SEED_CHUNKS:
            tenant = SEED_CHUNK_TENANTS.get(chunk.document_id, "")
            by_key.setdefault((chunk.document_id, tenant), []).append(chunk)
        for (document_id, tenant), chunks in by_key.items():
            self.put(Document(id=document_id, title=document_id, uri="", tenant=tenant), chunks)

    # ------------------------------------------------------------------ #
    # CitationStorePort
    # ------------------------------------------------------------------ #
    def put(self, document: Document, chunks: Sequence[DocumentChunk]) -> int:
        document_id = document.id
        tenant = document.tenant
        rows = [
            (
                document_id,
                tenant,
                chunk.ordinal,
                chunk.text,
                chunk.page,
                chunk.anchor,
                str(chunk.kind),
                None if chunk.bbox is None else chunk.bbox.x0,
                None if chunk.bbox is None else chunk.bbox.y0,
                None if chunk.bbox is None else chunk.bbox.x1,
                None if chunk.bbox is None else chunk.bbox.y1,
                chunk.embedding_ref,
            )
            for chunk in chunks
        ]
        with self._lock:
            self._conn.execute(
                "DELETE FROM document_chunks WHERE document_id = ? AND tenant = ?",
                (document_id, tenant),
            )
            self._conn.executemany(
                "INSERT INTO document_chunks "
                "(document_id, tenant, ordinal, text, page, anchor, kind, x0, y0, x1, y1, "
                "embedding_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def get(self, document_id: str, tenant: str | None = None) -> list[DocumentChunk]:
        with self._lock:
            if tenant is not None:
                cursor = self._conn.execute(
                    "SELECT * FROM document_chunks WHERE document_id = ? AND tenant = ? "
                    "ORDER BY ordinal",
                    (document_id, tenant),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY ordinal",
                    (document_id,),
                )
            rows = cursor.fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def delete(self, document_id: str, tenant: str | None = None) -> None:
        with self._lock:
            if tenant is not None:
                self._conn.execute(
                    "DELETE FROM document_chunks WHERE document_id = ? AND tenant = ?",
                    (document_id, tenant),
                )
            else:
                self._conn.execute(
                    "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
                )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> DocumentChunk:
        coords = (row["x0"], row["y0"], row["x1"], row["y1"])
        bbox = (
            BoundingBox(x0=coords[0], y0=coords[1], x1=coords[2], y1=coords[3])
            if all(c is not None for c in coords)
            else None
        )
        try:
            kind = BlockKind(row["kind"] or "paragraph")
        except ValueError:  # pragma: no cover - defensive against a hand-edited row
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
