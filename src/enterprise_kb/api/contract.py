"""Owned, versioned identifiers for the Hrz2 governed-RAG remote contract."""

from __future__ import annotations

CONTRACT_ID = "hrz2.governed-rag"
CONTRACT_VERSION = "1.0"
CONTRACT_MANIFEST_PATH = "/.well-known/governed-rag-contract"
OPENAPI_PATH = "/openapi.json"
SEARCH_PATH = "/v1/search"
ANSWER_PATH = "/v1/answer"


def contract_manifest() -> dict[str, object]:
    """Return a transport-neutral discovery document for sibling-service clients."""
    return {
        "id": CONTRACT_ID,
        "version": CONTRACT_VERSION,
        "openapi": OPENAPI_PATH,
        "operations": {
            "search": {"method": "POST", "path": SEARCH_PATH},
            "answer": {"method": "POST", "path": ANSWER_PATH},
        },
        "identity": {
            "actor_source": "server-verified",
            "tenant_source": "server-verified",
            "acl_principals": "optional-narrowing-only",
        },
        "invariants": [
            "responses contain only tenant-and-ACL-admitted evidence",
            "every answer carries citations and human-review state",
            "missing grounding refuses or returns an explicitly escalated envelope",
        ],
    }


__all__ = [
    "ANSWER_PATH",
    "CONTRACT_ID",
    "CONTRACT_MANIFEST_PATH",
    "CONTRACT_VERSION",
    "OPENAPI_PATH",
    "SEARCH_PATH",
    "contract_manifest",
]
