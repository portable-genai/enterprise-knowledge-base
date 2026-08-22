"""Local LLM adapter (LLMPort) : a deterministic, schema-driven generator.

The ``local`` profile's stand-in for **Gemini**: no model, no network, fully
reproducible. It reads ``request.response_schema`` (the JSON schema the calling service
asks for) and emits a deterministic JSON object whose keys match it, including
``used_document_ids`` recovered from the ``[document_id p.N]`` headers present in the
rendered passage block, plus a plausible ``classify``. There is no Google emulator for
Gemini, so this path is unconditional.

The schema-driven ``FakeLLM`` is a real, registered adapter rather than a test fixture, so
the in-memory implementation lives once under ``adapters/local`` and drives both the offline
tests and the CLI.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...config import Settings
from ...domain.models import (
    LlmRequest,
    LlmResponse,
    TokenUsage,
)

# The rendered passage block keys each source with ``[document_id p.N]`` headers; recover
# the ids the service actually grounded on so the answer cites only retrieved documents.
_SOURCE_HEADER_RE = re.compile(r"\[([a-z0-9][a-z0-9\-]*?)(?:\s+p\.[^\]]+)?\]")


def _schema_properties(schema: dict | None) -> dict[str, Any]:
    if not schema:
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


class LocalDeterministicLLMAdapter:
    """Deterministic LLM whose ``generate`` returns JSON matching the request schema.

    The two schemas A2 declares are flat objects: the grounded-answer schema
    (``answer`` / ``used_document_ids`` / ``confidence``) and the self-critique schema
    (``grounded`` / ``confidence`` / ``caveats``). The adapter emits exactly the declared
    fields, referencing the document ids actually present in the prompt via
    ``used_document_ids`` so the service maps page-level citations from real passages.
    """

    REASONING_MODEL = "gemini-3.5-flash"
    TRIAGE_MODEL = "gemini-3.1-flash-lite"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._reasoning_model = settings.models.reasoning or self.REASONING_MODEL
        self._triage_model = settings.models.triage or self.TRIAGE_MODEL

    # ------------------------------------------------------------------ #
    # LLMPort
    # ------------------------------------------------------------------ #
    def generate(self, request: LlmRequest) -> LlmResponse:
        document_ids = self._document_ids_from_request(request)
        body = self._body_for_schema(request.response_schema, document_ids)
        return LlmResponse(
            text=json.dumps(body),
            usage=TokenUsage(input_tokens=128, output_tokens=64, thinking_tokens=32),
            model=request.model or self._reasoning_model,
            web_citations=(),
            raw=body,
        )

    def classify(self, text: str, labels: list[str]) -> str:
        # Deterministic triage: first label (the services only use this for routing).
        return labels[0] if labels else ""

    # ------------------------------------------------------------------ #
    # Schema-driven body
    # ------------------------------------------------------------------ #
    def _document_ids_from_request(self, request: LlmRequest) -> list[str]:
        user = ""
        for message in reversed(request.messages):
            if message.role == "user":
                user = message.content
                break
        seen: list[str] = []
        for did in _SOURCE_HEADER_RE.findall(user):
            if did not in seen:
                seen.append(did)
        return seen

    def _flat_field(self, name: str, document_ids: list[str]) -> Any:
        return {
            "answer": (
                "Before onboarding a cloud provider, conduct provider due diligence "
                "covering data residency, exit strategy and concentration risk, and "
                "retain audit rights."
            ),
            "confidence": 0.86,
            "used_document_ids": list(document_ids),
            "citations": list(document_ids),
            "caveats": ["Verify the current version of each document."],
            "grounded": True,
            "groundedness": 0.9,
            "supported": True,
        }.get(name, "")

    def _body_for_schema(self, schema: dict | None, document_ids: list[str]) -> dict[str, Any]:
        props = _schema_properties(schema)
        if not props:
            return {
                "answer": self._flat_field("answer", document_ids),
                "confidence": 0.86,
                "used_document_ids": list(document_ids),
                "grounded": True,
                "caveats": [],
            }
        return {name: self._flat_field(name, document_ids) for name in props}
