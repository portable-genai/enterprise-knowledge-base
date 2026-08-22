"""AlloyDB freshness + residency ledger adapter (FreshnessLedgerPort).

Backs the domain ``FreshnessLedgerPort`` with **AlloyDB for PostgreSQL** (the same
single-region, CMEK-protected instance that holds the chunk vector store). Each row
records one document's residency region (P-03), fetched/expiry timestamps (the freshness
window), version, checksum and status, so a read can tell whether a document is fresh and
in-region before it is cited.

The AlloyDB connector + SQLAlchemy imports are lazy so the on-prem and test profiles
import this module with no GCP SDK installed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...config import Settings
from ...domain.models import FreshnessRecord, FreshnessStatus, utcnow
from ._alloydb import connection_options


class AlloyDBLedgerAdapter:
    """Read/write the document freshness + residency ledger in AlloyDB."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cfg = settings.alloydb
        self._table = self._cfg.table
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
    # FreshnessLedgerPort
    # ------------------------------------------------------------------ #
    def get(self, document_id: str, tenant: str = "") -> FreshnessRecord | None:
        import sqlalchemy

        engine = self._get_engine()
        with engine.connect() as conn:
            row = (
                conn.execute(
                    sqlalchemy.text(
                        f"SELECT * FROM {self._table} WHERE document_id = :id AND tenant = :tenant"
                    ),
                    {"id": document_id, "tenant": tenant},
                )
                .mappings()
                .one_or_none()
            )
        return self._row_to_record(row) if row else None

    def upsert(self, record: FreshnessRecord) -> None:
        import sqlalchemy

        engine = self._get_engine()
        stmt = sqlalchemy.text(
            f"""
            INSERT INTO {self._table}
                (document_id, tenant, residency_region, fetched_at, expires_at,
                 version, checksum, status, source_authority)
            VALUES (:document_id, :tenant, :residency_region, :fetched_at, :expires_at,
                    :version, :checksum, :status, :source_authority)
            ON CONFLICT (document_id, tenant) DO UPDATE SET
                residency_region = EXCLUDED.residency_region,
                fetched_at = EXCLUDED.fetched_at,
                expires_at = EXCLUDED.expires_at,
                version = EXCLUDED.version,
                checksum = EXCLUDED.checksum,
                status = EXCLUDED.status,
                source_authority = EXCLUDED.source_authority
            """
        )
        with engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "document_id": record.document_id,
                    "tenant": record.tenant,
                    "residency_region": record.residency_region,
                    "fetched_at": record.fetched_at,
                    "expires_at": record.expires_at,
                    "version": record.version,
                    "checksum": record.checksum,
                    "status": record.status.value,
                    "source_authority": record.source_authority,
                },
            )

    def delete(self, document_id: str, tenant: str = "") -> None:
        import sqlalchemy

        engine = self._get_engine()
        stmt = sqlalchemy.text(
            f"DELETE FROM {self._table} WHERE document_id = :id AND tenant = :tenant"
        )
        with engine.begin() as conn:
            conn.execute(stmt, {"id": document_id, "tenant": tenant})

    def list_expired(self, now: datetime | None = None) -> list[FreshnessRecord]:
        import sqlalchemy

        now = now or utcnow()
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                sqlalchemy.text(f"SELECT * FROM {self._table} WHERE expires_at <= :now"),
                {"now": now},
            ).mappings()
            return [self._row_to_record(r) for r in rows]

    def all(self) -> list[FreshnessRecord]:
        import sqlalchemy

        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(sqlalchemy.text(f"SELECT * FROM {self._table}")).mappings()
            return [self._row_to_record(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_record(row: Any) -> FreshnessRecord:
        return FreshnessRecord(
            document_id=row["document_id"],
            residency_region=row["residency_region"],
            fetched_at=row["fetched_at"],
            expires_at=row["expires_at"],
            tenant=row["tenant"] or "",
            version=row["version"],
            checksum=row["checksum"],
            status=FreshnessStatus(row["status"]),
            source_authority=str(row.get("source_authority") or "direct"),
        )
