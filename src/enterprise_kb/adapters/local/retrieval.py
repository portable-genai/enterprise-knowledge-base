"""Local retrieval adapter (RetrievalPort) : SQLite FTS5 over the ACL-tagged corpus.

The ``local`` profile's stand-in for **AlloyDB PostgreSQL FTS**: a ``sqlite3`` database with an
**FTS5** virtual table over the passages, queried with BM25 (``ORDER BY rank``). It is
SDK-free, deterministic and **seedable**, so the same code grounds the offline CLI run
and the unit tests. This path is
unconditional (no emulator branch).

The adapter returns the same :class:`RetrievedPassage` objects with page-level
:class:`Citation` provenance **and the document's ACL tags**, so the domain ACL filter
(P-09) can admit or drop each passage exactly as it does against the managed adapter. It
self-seeds from the built-in synthetic corpus on first use so an out-of-the-box local
run grounds answers without any ingestion step; callers may also ``seed(passages)`` or
ingest a corpus of their own.

Default DB path is under a per-package local dir (``~/.enterprise_kb/local.db``); tests
pass ``:memory:`` for an ephemeral, deterministic index.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

from ...config import Settings
from ...domain.models import (
    AclTag,
    BoundingBox,
    Citation,
    KbQuery,
    RetrievedPassage,
)
from ._seed import SEED_PASSAGES

# Default on-disk location for the local index (overridable via settings.local.db_path).
_DEFAULT_DB_DIR = Path.home() / ".enterprise_kb"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "local.db"

# FTS5 query syntax is strict; keep only word characters so a free-text query never trips
# an "fts5: syntax error" (e.g. on punctuation), and OR the terms for recall.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# ACL tag labels are joined into a single UNINDEXED column with this separator on write
# and split back out on read. A tab keeps the labels (which contain ':') unambiguous.
_TAG_SEP = "\t"

# A citation's bounding box is stored as a single UNINDEXED "x0,y0,x1,y1" string (SQLite
# FTS5 columns are all text anyway); an empty value means the passage has no box.
_BBOX_SEP = ","


class LocalFtsRetrievalAdapter:
    """Retrieve grounded, ACL-tagged passages from a local SQLite FTS5 index (BM25)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_path = getattr(getattr(settings, "local", None), "db_path", "") or str(_DEFAULT_DB_PATH)
        self._db_path = db_path
        # A single process-wide connection is reached from many of Starlette's threadpool
        # worker threads (api.deps.get_container is lru_cache'd), so open with
        # check_same_thread=False and serialise every access behind a lock
        # (single-writer) to keep SQLite single-threaded in practice.
        self._lock = threading.Lock()
        self._conn = self._connect(db_path)
        self._init_schema()
        # Self-seed the built-in corpus so an out-of-the-box local run is grounded.
        if self._is_empty():
            self.seed(SEED_PASSAGES)

    # ------------------------------------------------------------------ #
    # Connection / schema
    # ------------------------------------------------------------------ #
    @staticmethod
    def _connect(db_path: str) -> sqlite3.Connection:
        if db_path not in (":memory:", "") and not db_path.startswith("file:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        # One FTS5 table holds the searchable text; citation + ACL metadata ride alongside
        # as UNINDEXED columns so a single query returns everything needed to cite a hit
        # and let the domain make the ACL admission decision.
        with self._lock:
            # Migrate a stale on-disk index that predates the ``tenant`` or the anchor
            # columns: an FTS5 table cannot be ALTERed to add a column, so drop it and let
            # the caller re-seed (the built-in corpus self-seeds; a persisted corpus is a
            # rebuildable cache, never the system of record).
            existing = self._table_columns()
            if existing and not {"tenant", "anchor", "bbox"} <= existing:
                self._conn.execute("DROP TABLE IF EXISTS passages")
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(
                    text,
                    document_id UNINDEXED,
                    title UNINDEXED,
                    uri UNINDEXED,
                    version UNINDEXED,
                    page UNINDEXED,
                    score UNINDEXED,
                    acl_tags UNINDEXED,
                    tenant UNINDEXED,
                    anchor UNINDEXED,
                    bbox UNINDEXED
                )
                """
            )
            self._conn.commit()

    def _table_columns(self) -> set[str]:
        """Column names of the existing ``passages`` table (empty set if it is absent)."""
        try:
            rows = self._conn.execute("PRAGMA table_info('passages')").fetchall()
        except sqlite3.Error:  # pragma: no cover - defensive
            return set()
        return {str(r[1]) for r in rows}

    def _is_empty(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT count(*) AS n FROM passages").fetchone()
        return int(row["n"]) == 0

    # ------------------------------------------------------------------ #
    # Seeding / ingestion
    # ------------------------------------------------------------------ #
    def seed(self, passages: tuple[RetrievedPassage, ...] | list[RetrievedPassage]) -> int:
        """Replace the index contents with ``passages`` (deterministic test/CLI seed)."""
        with self._lock:
            self._conn.execute("DELETE FROM passages")
            return self._insert(list(passages))

    def add(self, passages: list[RetrievedPassage]) -> int:
        """Append ``passages`` to the index without clearing existing rows."""
        with self._lock:
            return self._insert(passages)

    def delete_document(self, document_id: str, tenant: str = "") -> None:
        """Drop passages for ``document_id`` from the index, scoped to ``tenant``.

        When ``tenant`` is set, only rows whose tenant matches are removed, so a caller can
        never delete another tenant's document by id. An empty ``tenant`` (trusted local
        tooling / single-tenant use) removes every row for the id, as before.
        """
        with self._lock:
            if tenant:
                self._conn.execute(
                    "DELETE FROM passages WHERE document_id = ? AND tenant = ?",
                    (document_id, tenant),
                )
            else:
                self._conn.execute("DELETE FROM passages WHERE document_id = ?", (document_id,))
            self._conn.commit()

    def _insert(self, passages: list[RetrievedPassage]) -> int:
        # Caller must hold ``self._lock`` (single-writer); this is the lock-free core
        # shared by ``seed`` and ``add``.
        rows = []
        for p in passages:
            c = p.citation
            tags = _TAG_SEP.join(t.label for t in p.acl_tags)
            rows.append(
                (
                    p.text,
                    c.document_id,
                    c.title,
                    c.uri,
                    c.version,
                    "" if c.page is None else str(c.page),
                    f"{p.score:.6f}",
                    tags,
                    p.tenant,
                    c.anchor or "",
                    _bbox_to_str(c.bbox),
                )
            )
        self._conn.executemany(
            "INSERT INTO passages "
            "(text, document_id, title, uri, version, page, score, acl_tags, tenant, "
            "anchor, bbox) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    # ------------------------------------------------------------------ #
    # RetrievalPort
    # ------------------------------------------------------------------ #
    def retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        """Return ranked, ACL-tagged passages with citations for ``query``.

        The adapter may pre-filter by a structured ``source_system`` filter, but the
        ACL admission decision stays in the domain (P-09): every admitted-by-text
        passage is returned carrying its ACL tags for the service to re-filter.
        """
        match = self._build_match(query.text)
        top_k = max(query.top_k, 1)

        with self._lock:
            if not match:
                # No usable query terms: fall back to a score-ordered scan so the pipeline
                # still gets something deterministic rather than an FTS5 syntax error.
                cursor = self._conn.execute(
                    "SELECT * FROM passages ORDER BY score DESC LIMIT ?", (top_k,)
                )
            else:
                cursor = self._conn.execute(
                    "SELECT * FROM passages WHERE passages MATCH ? ORDER BY rank LIMIT ?",
                    (match, top_k),
                )
            rows = cursor.fetchall()

        return [self._row_to_passage(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_match(text: str) -> str:
        """Build a safe FTS5 MATCH expression: OR of the alphanumeric query tokens."""
        tokens = _TOKEN_RE.findall(text or "")
        if not tokens:
            return ""
        # Quote each token so reserved words (AND/OR/NOT/NEAR) are treated as literals.
        return " OR ".join(f'"{t}"' for t in tokens)

    @staticmethod
    def _row_to_passage(row: sqlite3.Row) -> RetrievedPassage:
        page_raw = row["page"]
        page = int(page_raw) if page_raw not in (None, "") else None
        try:
            score = float(row["score"])
        except (TypeError, ValueError):
            score = 0.0
        raw_tags = row["acl_tags"] or ""
        acl_tags = tuple(AclTag(label=t) for t in raw_tags.split(_TAG_SEP) if t)
        citation = Citation(
            document_id=row["document_id"],
            title=row["title"],
            uri=row["uri"],
            version=row["version"] or "unknown",
            page=page,
            snippet=(row["text"] or "")[:280],
            score=score,
            anchor=row["anchor"] or None,
            bbox=_bbox_from_str(row["bbox"]),
        )
        return RetrievedPassage(
            text=row["text"],
            citation=citation,
            score=score,
            acl_tags=acl_tags,
            tenant=row["tenant"] or "",
        )


def _bbox_to_str(bbox: BoundingBox | None) -> str:
    """Encode a bounding box as ``"x0,y0,x1,y1"``; empty string when there is none."""
    if bbox is None:
        return ""
    return _BBOX_SEP.join(f"{v:.6f}" for v in bbox.as_tuple())


def _bbox_from_str(raw: str | None) -> BoundingBox | None:
    """Decode a stored bounding box; ``None`` for an absent or malformed value."""
    if not raw:
        return None
    parts = raw.split(_BBOX_SEP)
    if len(parts) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(p) for p in parts)
    except ValueError:  # pragma: no cover - defensive against a hand-edited row
        return None
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)
