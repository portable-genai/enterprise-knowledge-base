"""Local freshness + residency ledger adapter (FreshnessLedgerPort) : SQLite store.

The ``local`` profile's stand-in for the **AlloyDB** freshness/residency ledger: a small
SQLite table tracking, per document, its residency region (P-03), when it was fetched and
when it expires (the freshness window), version, checksum and status. Seedable and
deterministic. When the Firestore emulator is opted in (``FIRESTORE_EMULATOR_HOST`` set
AND the client lib imports), the adapter mirrors writes to it; the google client is
imported lazily, only on that branch, so the default path imports no google-cloud package.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from ...config import Settings
from ...domain.models import FreshnessRecord, FreshnessStatus, utcnow
from ._emulator import firestore_emulator_active

_DEFAULT_DB_DIR = Path.home() / ".enterprise_kb"
_DEFAULT_LEDGER_PATH = _DEFAULT_DB_DIR / "ledger.db"


class LocalLedgerAdapter:
    """SQLite freshness + residency ledger (Firestore-emulator-backed when opted in)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        path = getattr(getattr(settings, "local", None), "ledger_path", "") or str(
            _DEFAULT_LEDGER_PATH
        )
        if path not in (":memory:", "") and not path.startswith("file:"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One process-wide connection is reached from many of Starlette's threadpool
        # worker threads (api.deps.get_container is lru_cache'd), so open with
        # check_same_thread=False and serialise every access behind a lock
        # (single-writer) to keep SQLite single-threaded in practice.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # Migrate a stale ledger that predates the tenant partition: the primary key moved
            # from (document_id) to (document_id, tenant), which sqlite cannot ALTER in place.
            # The ledger is a rebuildable cache (the refresh job repopulates it), so drop it.
            cols = {
                str(r[1]) for r in self._conn.execute("PRAGMA table_info('freshness')").fetchall()
            }
            if cols and "tenant" not in cols:
                self._conn.execute("DROP TABLE IF EXISTS freshness")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS freshness (
                    document_id      TEXT NOT NULL,
                    tenant           TEXT NOT NULL DEFAULT '',
                    residency_region TEXT NOT NULL,
                    fetched_at       TEXT NOT NULL,
                    expires_at       TEXT NOT NULL,
                    version          TEXT NOT NULL,
                    checksum         TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    source_authority TEXT NOT NULL DEFAULT 'direct',
                    PRIMARY KEY (document_id, tenant)
                )
                """
            )
            current_cols = {
                str(r[1]) for r in self._conn.execute("PRAGMA table_info('freshness')").fetchall()
            }
            if "source_authority" not in current_cols:
                self._conn.execute(
                    "ALTER TABLE freshness ADD COLUMN source_authority "
                    "TEXT NOT NULL DEFAULT 'direct'"
                )
            self._conn.commit()
        self._fs = None
        if firestore_emulator_active():
            from google.cloud import firestore  # noqa: PLC0415

            self._fs = firestore.Client(project=settings.project_id or "local")

    def get(self, document_id: str, tenant: str = "") -> FreshnessRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM freshness WHERE document_id = ? AND tenant = ?",
                (document_id, tenant),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def upsert(self, record: FreshnessRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO freshness "
                "(document_id, tenant, residency_region, fetched_at, expires_at, version, "
                "checksum, status, source_authority) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(document_id, tenant) DO UPDATE SET "
                "residency_region=excluded.residency_region, fetched_at=excluded.fetched_at, "
                "expires_at=excluded.expires_at, version=excluded.version, "
                "checksum=excluded.checksum, status=excluded.status, "
                "source_authority=excluded.source_authority",
                (
                    record.document_id,
                    record.tenant,
                    record.residency_region,
                    record.fetched_at.isoformat(),
                    record.expires_at.isoformat(),
                    record.version,
                    record.checksum,
                    record.status.value,
                    record.source_authority,
                ),
            )
            self._conn.commit()
        if self._fs is not None:
            self._fs.collection("freshness").document(f"{record.tenant}::{record.document_id}").set(
                {
                    "document_id": record.document_id,
                    "tenant": record.tenant,
                    "residency_region": record.residency_region,
                    "status": record.status.value,
                    "source_authority": record.source_authority,
                }
            )

    def delete(self, document_id: str, tenant: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM freshness WHERE document_id = ? AND tenant = ?",
                (document_id, tenant),
            )
            self._conn.commit()
        if self._fs is not None:
            self._fs.collection("freshness").document(f"{tenant}::{document_id}").delete()

    def list_expired(self, now: datetime | None = None) -> list[FreshnessRecord]:
        now = now or utcnow()
        return [r for r in self.all() if r.expires_at <= now]

    def all(self) -> list[FreshnessRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM freshness ORDER BY document_id ASC").fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FreshnessRecord:
        return FreshnessRecord(
            document_id=row["document_id"],
            residency_region=row["residency_region"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            tenant=row["tenant"] or "",
            version=row["version"],
            checksum=row["checksum"],
            status=FreshnessStatus(row["status"]),
            source_authority=row["source_authority"] or "direct",
        )
