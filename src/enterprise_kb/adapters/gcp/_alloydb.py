"""Shared fail-closed AlloyDB IAM-auth connection inputs for managed adapters.

Each workload runs as its own service account and Terraform creates the matching AlloyDB IAM
database user. The connector exchanges Application Default Credentials for the short-lived
database credential; no database password exists in settings, Secret Manager, Terraform state or
the serving environment. Keep this validation seam SDK-free and use it from every AlloyDB adapter.
"""

from __future__ import annotations

from typing import TypedDict

from ...config import AlloyDBSettings


class AlloyDBConnectionOptions(TypedDict):
    user: str
    db: str
    ip_type: str
    enable_iam_auth: bool


def connection_options(settings: AlloyDBSettings) -> AlloyDBConnectionOptions:
    """Return connector keyword arguments or refuse an incomplete managed configuration."""
    required = {
        "instance_uri": settings.instance_uri.strip(),
        "database": settings.database.strip(),
        "user": settings.user.strip(),
        "ip_type": settings.ip_type.strip(),
    }
    missing = sorted(name for name, value in required.items() if not str(value).strip())
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(
            f"AlloyDB IAM connection is incomplete ({names}); set KB_ALLOYDB_USER to this "
            "workload's Terraform-created IAM database user and configure the instance URI "
            "before serving the gcp profile"
        )
    return {
        "user": required["user"],
        "db": required["database"],
        "ip_type": required["ip_type"],
        "enable_iam_auth": True,
    }


__all__ = ["AlloyDBConnectionOptions", "connection_options"]
