"""JSON-safe serialization for domain objects.

``to_jsonable(obj)`` converts dataclasses, enums, datetimes and nested containers
into plain JSON-serializable Python (``dict`` / ``list`` / ``str`` / ``int`` /
``float`` / ``bool`` / ``None``). Used by remote-platform clients (to build the
horizontal-platform HTTP payloads) and by the API layer to render the KB artifacts
(passages, grounded answer, ingest result).

**Sourced from the shared ``hex-service-kit`` commons.** The walker used
to live here as a copy; it is now re-exported from :mod:`hex_service_kit.serialization`
(same rules, SPEC §5 / §6: enum ``.value``, ISO datetimes, dataclass field dicts, tuples
to lists, stringified keys, never raises), so the wire/audit encoding is one
implementation across the catalog. Pure standard library; no cloud or framework imports.
"""

from __future__ import annotations

from typing import Any

from hex_service_kit.serialization import to_jsonable

__all__ = ["citation_to_dict", "to_jsonable"]


def citation_to_dict(citation: Any) -> dict[str, Any]:
    """Serialize a ``Citation`` (or any citation-like dataclass) to a plain dict.

    Convenience wrapper that guarantees a ``dict`` result for the citation arrays in
    audit events and the KB artifacts (SPEC §6 ``AuditEvent.citations``). Accepts any
    dataclass/enum-bearing object and delegates to :func:`to_jsonable`.
    """
    result = to_jsonable(citation)
    if isinstance(result, dict):
        return result
    return {"value": result}
