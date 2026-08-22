"""Reviewed, portable principal-to-tag binding synchronization for managed ACL reads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .fetch import download_gcs_bytes

MAX_ACL_BINDINGS_BYTES = 1 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AclBinding:
    tenant: str
    principal_id: str
    tags: tuple[str, ...]


def parse_bindings(raw: str) -> tuple[AclBinding, ...]:
    """Strictly validate the bank-reviewed portable JSON artifact."""
    data = json.loads(raw)
    entries = data.get("bindings") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("ACL artifact must contain a non-empty bindings list")
    bindings: list[AclBinding] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"tenant", "principal_id", "tags"}:
            raise ValueError("each ACL binding must contain exactly tenant, principal_id and tags")
        tenant = str(entry["tenant"]).strip()
        principal = str(entry["principal_id"]).strip()
        tags_raw = entry["tags"]
        if not tenant or not principal or not isinstance(tags_raw, list):
            raise ValueError("ACL tenant/principal must be non-empty and tags must be a list")
        tags = tuple(dict.fromkeys(str(tag).strip() for tag in tags_raw if str(tag).strip()))
        if not tags or (tenant, principal) in seen:
            raise ValueError("ACL binding tags must be non-empty and tenant/principal unique")
        seen.add((tenant, principal))
        bindings.append(AclBinding(tenant, principal, tags))
    return tuple(bindings)


def _download(uri: str) -> str:
    return download_gcs_bytes(uri, MAX_ACL_BINDINGS_BYTES, "ACL bindings").decode("utf-8")


def sync_acl_bindings(container: Any) -> int:
    """Atomically replace the ACL projection under the dedicated pipeline IAM user."""
    if container.settings.profile not in {"gcp", "platform"}:
        return 0
    bindings = parse_bindings(_download(container.settings.acl_sync.bindings_uri))
    adapter = container.access_control
    engine = adapter._get_engine()  # pipeline-bound adapter; serving identity never calls sync
    import sqlalchemy

    rows = [
        {"tenant": b.tenant, "principal_id": b.principal_id, "tag_label": tag}
        for b in bindings
        for tag in b.tags
    ]
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("DELETE FROM principal_acl_tags"))
        conn.execute(
            sqlalchemy.text(
                """
                INSERT INTO principal_acl_tags (tenant, principal_id, tag_label, enabled)
                VALUES (:tenant, :principal_id, :tag_label, TRUE)
                """
            ),
            rows,
        )
    return len(rows)
