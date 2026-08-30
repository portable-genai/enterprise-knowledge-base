"""Shared grounded retrieve-render-cite routine (private to the domain layer).

The search and grounded-answer pipelines share the same passage machinery: render
ACL-admitted passages into the prompt context, call the LLM with a structured-output
schema, defensively parse the JSON, and map the model's ``used_document_ids`` back to
the retrieved passages' ``Citation`` objects (preserving page provenance).

This module factors out that machinery so each service keeps the exact constructor and
method signature mandated by SPEC §5 while sharing one well-tested core. It is
``_``-prefixed and not part of the public domain API.

Pure domain code : talks only to ports and models, no Google Cloud / ADK imports.
"""

from __future__ import annotations

import json
from typing import Any

from .models import (
    Citation,
    KbQuery,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    RetrievedPassage,
    ThinkingLevel,
)
from .prompts import PASSAGE_BLOCK


def render_passages(passages: list[RetrievedPassage]) -> str:
    """Render retrieved passages into the numbered context block for the prompt.

    Each block is keyed by ``document_id`` and page so the model can echo
    ``[document_id p.N]`` citations exactly. Page is rendered as ``?`` when unknown so
    the model emits ``[document_id]`` rather than inventing a page.
    """
    if not passages:
        return "(no passages were retrieved)"
    blocks: list[str] = []
    for p in passages:
        c = p.citation
        page = str(c.page) if c.page is not None else "?"
        blocks.append(
            PASSAGE_BLOCK.format(
                document_id=c.document_id,
                page=page,
                title=c.title,
                version=c.version,
                text=p.text.strip(),
            )
        )
    return "\n".join(blocks)


def retrieve_passages(
    retrieval: Any,
    query_text: str,
    allowed_tags: set[str] | tuple[str, ...] = (),
    tenant: str = "",
    filters: dict[str, str] | None = None,
    top_k: int = 10,
) -> list[RetrievedPassage]:
    """Run a retrieval query through the RetrievalPort defensively.

    The query carries only server-resolved ACL tags and the verified tenant, never model- or
    client-asserted principals. An adapter may use them for server-side candidate pushdown;
    the domain still re-filters every result (P-09).
    """
    query = KbQuery(
        text=query_text,
        top_k=top_k,
        allowed_tags=tuple(sorted(allowed_tags)),
        tenant=tenant,
        filters=dict(filters or {}),
    )
    passages = retrieval.retrieve(query)
    return list(passages or [])


def filter_by_tenant(
    passages: list[RetrievedPassage],
    tenant: str,
) -> list[RetrievedPassage]:
    """Drop passages outside the caller's tenant partition (multi-tenant isolation).

    A passage whose ``tenant`` equals the caller's is admissible; a passage with an empty
    ``tenant`` is shared/global content, visible to every tenant. **A caller with no tenant
    (``""``) is therefore admitted to the shared corpus and to nothing else**, which is the
    fail-closed direction the rest of the fleet already reads an empty tenant in.

    It used to be the opposite here: an empty tenant returned every passage unpartitioned,
    on the reading that only trusted local tooling ever arrives without one. That made the
    absence of a value a grant of the widest possible read, so any future path that resolved
    a tenant to ``""`` would have widened silently rather than refused. The org decision of
    2026-08-30 closed it by deleting the exemption, not by guarding its callers: the MCP
    server (``mcp/server.py``) asserts no tenant precisely because its transport verifies no
    end user, and its own contract already says such a caller reads the public corpus.

    In secure/multi-tenant deployments the caller's tenant always comes from the
    server-verified :class:`Principal`, so a caller can never read another tenant's corpus by
    asserting a different tenant.
    """
    return [p for p in passages if p.tenant in ("", tenant)]


def prefer_unambiguous_document_owners(
    passages: list[RetrievedPassage],
    tenant: str,
) -> list[RetrievedPassage]:
    """Prevent a shared and tenant-owned document id from aliasing one another.

    Citation and model contracts intentionally expose the bank's ``document_id`` rather
    than a storage key.  The stores, however, key content by ``(document_id, tenant)``.
    When a shared document and a tenant override use the same id, selecting both would
    make the model's id-only citation ambiguous and could resolve an anchor from the
    wrong tenant.  For a verified tenant, its own document deterministically shadows the
    shared version.  Trusted tenant-less tooling cannot make that choice, so every owner
    of an ambiguous id is dropped fail-closed.
    """
    owners_by_id: dict[str, set[str]] = {}
    for passage in passages:
        owners_by_id.setdefault(passage.citation.document_id, set()).add(passage.tenant)

    selected_owner: dict[str, str | None] = {}
    for document_id, owners in owners_by_id.items():
        if len(owners) == 1:
            selected_owner[document_id] = next(iter(owners))
        elif tenant and tenant in owners:
            selected_owner[document_id] = tenant
        else:
            selected_owner[document_id] = None

    return [
        passage
        for passage in passages
        if selected_owner.get(passage.citation.document_id) == passage.tenant
    ]


def filter_by_allowed_tags(
    passages: list[RetrievedPassage],
    allowed_tags: set[str],
) -> list[RetrievedPassage]:
    """Keep only passages admitted by the caller's resolved ACL tag set (P-09).

    A passage is admissible only when **every** one of its ``acl_tags`` is in
    ``allowed_tags`` (all-of / subset matching): a passage tagged
    ``{dept:risk, classification:restricted}`` requires the caller to hold *both* tags,
    so a caller entitled to only one of a passage's dimensions is not admitted. A passage
    that carries **no** tags is treated as restricted and dropped (fail-closed):
    unlabelled content must not leak to a caller whose access we cannot positively
    establish. The ACL decision lives here in the domain, never in a retrieval adapter.
    """
    if not allowed_tags:
        return []
    out: list[RetrievedPassage] = []
    for p in passages:
        tags = {t.label for t in p.acl_tags}
        if tags and tags <= allowed_tags:
            out.append(p)
    return out


def parse_structured(response: LlmResponse) -> dict[str, Any]:
    """Parse an LLM structured-output response into a dict, defensively.

    The GCP adapter returns the structured JSON as ``LlmResponse.text`` when a
    ``response_schema`` is set. We ``json.loads`` it; on any failure (plain text,
    truncation, a fenced block) we fall back to extracting the first balanced JSON
    object, and finally to an empty dict so callers degrade gracefully rather than
    raising on a malformed model reply.
    """
    text = (response.text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"items": parsed}
    except (json.JSONDecodeError, ValueError):
        pass

    snippet = _extract_json_object(text)
    if snippet is not None:
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` block in ``text``, or None."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def citations_for_document_ids(
    used_document_ids: list[str],
    passages: list[RetrievedPassage],
) -> tuple[Citation, ...]:
    """Map model-returned ``used_document_ids`` back to retrieved passage Citations.

    Preserves the page-level provenance from retrieval (the model only returns ids,
    never pages). When a document_id was cited by multiple passages, each distinct
    (document_id, page) citation is kept once, in retrieval order. Unknown ids the
    model may have hallucinated are dropped : we only ever cite what we retrieved.
    """
    by_id: dict[str, list[Citation]] = {}
    for p in passages:
        by_id.setdefault(p.citation.document_id, []).append(p.citation)

    wanted = list(used_document_ids or [])
    # If the model returned nothing usable, fall back to all retrieved citations so an
    # answer is never left provenance-less.
    selected_ids = [did for did in wanted if did in by_id]
    if not selected_ids:
        selected_ids = list(by_id.keys())

    out: list[Citation] = []
    seen: set[tuple[str, int | None]] = set()
    for did in selected_ids:
        for citation in by_id.get(did, ()):
            key = (citation.document_id, citation.page)
            if key not in seen:
                seen.add(key)
                out.append(citation)
    return tuple(out)


def build_llm_request(
    system_instruction: str,
    user_content: str,
    model: str | None,
    response_schema: dict | None,
    thinking: ThinkingLevel = ThinkingLevel.HIGH,
    temperature: float = 0.0,
    max_output_tokens: int = 4096,
) -> LlmRequest:
    """Assemble an ``LlmRequest`` with a single user message and a system prompt.

    ``model=None`` lets the adapter pick its configured default (the reasoning model,
    ``gemini-3.5-flash``); thinking defaults to HIGH for grounded reasoning per SPEC.
    """
    return LlmRequest(
        messages=(LlmMessage(role="user", content=user_content),),
        system_instruction=system_instruction,
        model=model,
        thinking=thinking,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_schema=response_schema,
    )


def maybe_record_usage(tracer: Any, response: Any) -> None:
    """Emit token usage to the tracer for FinOps, defensively (never fatal)."""
    try:
        usage = getattr(response, "usage", None)
        model = getattr(response, "model", "") or ""
        if usage is not None and hasattr(tracer, "record_token_usage"):
            tracer.record_token_usage(usage, model)
    except Exception:  # noqa: BLE001 - metrics must never break a generation path
        return


def as_str_list(value: Any) -> list[str]:
    """Coerce an arbitrary model value into a list of stripped non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []
