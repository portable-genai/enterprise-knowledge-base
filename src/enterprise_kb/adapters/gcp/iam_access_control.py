"""Managed principal-to-ACL-tag resolution for :class:`AccessControlPort`.

AlloyDB maps server-verified principal identifiers to ACL tags inside the caller's verified
tenant partition. Enterprise directory synchronization provisions direct user bindings (including
denormalized group entitlements) and may provision group bindings when the identity adapter
supplies a server-verified group id. This request path never expands an asserted group into other
identities. The domain owns the final authorization decision; this adapter only resolves
entitlements.

All managed imports are lazy so local and on-prem profiles remain SDK-free.
"""

from __future__ import annotations

import re
from typing import Any

from ...config import Settings
from ._alloydb import connection_options

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _table_identifier(value: str) -> str:
    """Validate a possibly schema-qualified SQL identifier before interpolation."""
    parts = value.split(".")
    if not parts or any(_IDENTIFIER.fullmatch(part) is None for part in parts):
        raise ValueError(f"invalid AlloyDB ACL table identifier: {value!r}")
    return ".".join(parts)


class IamAccessControlAdapter:
    """Resolve verified tenant principals through an AlloyDB binding table."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cfg = settings.alloydb
        self._table = _table_identifier(self._cfg.acl_table)
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        """Build the same private AlloyDB connection used by the other enterprise-knowledge-base
        stores.
        """
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

        # Schema creation is a migration-owner responsibility. The serving identity performs
        # SELECT only and therefore does not need CREATE privileges on the database.
        self._engine = sqlalchemy.create_engine(
            "postgresql+pg8000://", creator=_connect, pool_pre_ping=True, pool_timeout=10
        )
        return self._engine

    def resolve(self, principals: list[str], tenant: str) -> set[str]:
        """Resolve only the verified tenant's verified/narrowed principal identifiers.

        Missing tenants and empty principal sets are denied without touching a managed
        service. AlloyDB failures propagate, so the request fails closed rather than being
        relabelled as a successful empty lookup.
        """
        verified_tenant = tenant.strip()
        if not verified_tenant:
            return set()
        normalized = list(
            dict.fromkeys(principal.strip() for principal in principals if principal.strip())
        )
        if not normalized:
            return set()
        return self._tags_for_principals(set(normalized), verified_tenant)

    def _tags_for_principals(self, principals: set[str], tenant: str) -> set[str]:
        """Query tenant-scoped, enabled principal bindings from AlloyDB/PostgreSQL."""
        if not tenant or not principals:
            return set()

        import sqlalchemy

        statement = sqlalchemy.text(
            f"""
            SELECT DISTINCT tag_label
            FROM {self._table}
            WHERE tenant = :tenant
              AND principal_id IN :principals
              AND enabled IS TRUE
            """
        ).bindparams(sqlalchemy.bindparam("principals", expanding=True))
        params = {"tenant": tenant, "principals": tuple(sorted(principals))}
        with self._get_engine().connect() as conn:
            rows = conn.execute(statement, params).mappings().all()
        return {
            str(row["tag_label"]).strip()
            for row in rows
            if row.get("tag_label") is not None and str(row["tag_label"]).strip()
        }
