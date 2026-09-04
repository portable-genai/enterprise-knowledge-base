"""Shared server-verified identity values used by the governed retrieval domain.

Identity is a fleet contract rather than enterprise-knowledge-base-specific business logic.
Re-exporting the stdlib-only values from :mod:`hex_service_kit.identity` keeps the API, adapters and
domain on the same concrete ``Principal`` type while preserving enterprise-knowledge-base's
established import path.
"""

from hex_service_kit.identity import ANONYMOUS, IdentityError, Principal, RequestContext

__all__ = ["ANONYMOUS", "IdentityError", "Principal", "RequestContext"]
