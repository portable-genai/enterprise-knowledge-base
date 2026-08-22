"""FastAPI application for the A2 Enterprise Knowledge Base.

Exposes the governed RAG surface : ACL-aware search, grounded answer, document ingest /
delete : plus corpus-freshness, health and the versioned governed-RAG contract manifest.
The React/Next.js UI and sibling services consume this HTTP surface (SPEC §6).

Design constraints:

* **Import-safe.** Building the :class:`~enterprise_kb.config.Container` is deferred to
  request time via the ``deps`` factories, so importing this module (or ``app``) never
  touches Google Cloud. The on-prem/test profile imports it with no GCP SDK installed.
* **Guardrail blocks are not errors.** A :class:`GuardrailBlockedError` from the ingest
  service is translated to an HTTP 200 carrying a *blocked* envelope flagged for human
  review, never a 500.
* **An ungrounded answer is refused, not softened.** The domain raises
  :class:`RetrievalEmptyError` when nothing the caller may see grounds the query (B2);
  the handler below turns it into a well-formed HTTP 422 refusal flagged for human
  review, so a caller gets a structured, auditable "no" instead of a confident-looking
  answer with no citations.
* **Security headers on every response.** The shared
  ``hex_service_kit.web.add_security_headers`` middleware emits the CSP
  ``frame-ancestors`` + ``X-Frame-Options`` embedding controls, ``nosniff``,
  ``Referrer-Policy`` and (outside the local profile) HSTS (C6).
* **Region pinned** to ``asia-southeast1`` (Singapore) for data residency (SPEC §2).

Run locally with ``python -m enterprise_kb.api.app`` (uvicorn on :8082).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from hex_service_kit import cors_allowlist, resolve_bind_host
from hex_service_kit.web import add_loopback_exposure_guard, add_security_headers

from ..config import Settings, end_user_auth_kind
from ..domain import models as m
from ..domain.errors import GuardrailBlockedError, RetrievalEmptyError
from ..domain.identity import IdentityError
from ..domain.services import IngestionService, KnowledgeBaseService
from ..envread import read_env_setting
from ..managed_preflight import assert_managed_profile_ready
from ..ports.identity import VERIFIED
from . import deps
from .contract import ANSWER_PATH, CONTRACT_MANIFEST_PATH, SEARCH_PATH, contract_manifest
from .schemas import (
    AnswerRequest,
    AnswerResponse,
    CorpusStatusResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
)
from .security import CurrentPrincipal, ServiceCaller

# Local Next.js dev origins the browser UI is served from during development.
_DEV_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

# The resolved profile, read once at app construction (same source the container uses), and
# split into the two views the security layers need. Every RELAXATION below keys off
# ``exposure_profile``, never the raw profile: an unset KB_PROFILE is not consent, so it gets
# no dev CORS origins, no X-Dev-Persona header and HSTS on. The bind RESTRICTION in ``main()``
# keys off ``bind_profile``, which fails closed the other way and keeps loopback.
_CHOICE = deps.get_settings().choice
_EXPOSURE = _CHOICE.exposure_profile

# Embedding-surface controls. In secure/embedded mode the KB is served same-origin via the
# parent app's reverse-proxy (no CORS needed); for the cross-origin / standalone dev case,
# KB_CORS_ORIGINS is an explicit per-tenant allowlist (never "*").
# KB_FRAME_ANCESTORS is the CSP frame-ancestors allowlist of parent origins permitted to
# iframe the KB console UI.
_FRAME_ANCESTORS_ENV = "KB_FRAME_ANCESTORS"
_CORS_ORIGINS_ENV = "KB_CORS_ORIGINS"

#: Values that must never be accepted in either origin policy. Four spellings, not one:
#: ``'*'`` is the quoted form CSP also honours, ``*.*`` is the subdomain wildcard, and ``null``
#: is the origin a SANDBOXED iframe presents, so accepting it hands the console to any page that
#: can sandbox one.
#:
#: The set is only HALF the rule, because a set can match an entry exactly and nothing else. See
#: :func:`_refuse_wildcard` for the other half. Matching here stays exact, so a host that merely
#: spells one of these inside a longer name (``https://nullify.example``) is unaffected.
_ORIGIN_WILDCARDS = frozenset({"*", "'*'", "null", "*.*"})


def _refuse_wildcard(origins: Sequence[str], variable: str) -> None:
    """A wildcard in an origin policy is the policy switched off, so it never boots.

    Both allowlists resolved their unset and emptied states carefully and then passed the value
    on verbatim, so a wildcard reached ``CORSMiddleware(allow_origins=[...])`` and the CSP
    ``frame-ancestors`` directive. On an ACL-aware corpus, and with ``allow_credentials=True``,
    that lets any page on the internet frame the console and read its answers cross-origin. The
    prohibition was written down in a comment beside each variable, and in the shared kit's
    docstring for CORS, and enforced by neither.

    Raised where the value is RESOLVED, which is module import, so an operator whose config
    template rendered a wildcard finds out when the service refuses to start rather than when a
    browser somewhere exercises it.

    The exact-token set was not the whole rule. It can only match an entry EXACTLY, so a
    host-source form such as ``https://*.evil.example`` was in no set and travelled through
    both allowlists verbatim. CSP honours that form as EVERY subdomain, including one obtained
    by takeover or one serving user content, and CORS trusted the same shape WITH credentials
    on an ACL-aware corpus. So the rule is a UNION: an asterisk ANYWHERE in an entry, or the
    whole entry being one of :data:`_ORIGIN_WILDCARDS`. A legitimate origin never contains the
    character, so the added half refuses nothing a deployment could correctly hold, and the
    token half stays because ``null`` and ``'*'`` carry no asterisk at all.
    """
    offending = [origin for origin in origins if "*" in origin or origin in _ORIGIN_WILDCARDS]
    if offending:
        raise ValueError(
            f"{variable} contains {offending[0]!r}: the origin policy must never contain a "
            "wildcard. Name the exact parent or caller origins instead, or unset the variable "
            "to keep the shipped default."
        )


def _frame_ancestors(raw: str | None) -> str:
    """Three-state read of ``KB_FRAME_ANCESTORS``; an emptied value REFUSES to boot.

    Unset is not a member of the valid value set, so this resolves three states rather than
    two. Unset keeps the shipped ``'self'``. Set to a value naming no origin used to reach
    ``add_security_headers`` as ``""``, which emitted the header
    ``Content-Security-Policy: frame-ancestors`` with an EMPTY directive: browsers discard
    that as a parse error, and the ``== "'self'"`` branch that adds ``X-Frame-Options`` was
    skipped as well, so the clickjacking control vanished from both channels at once with
    nothing in the response to show it.

    An empty string is not a usable value for this read, so it refuses at boot rather than
    serving a posture nobody chose. A total lockdown stays expressible: set the variable to
    ``'none'``. Refusing is loud and immediate (uvicorn imports this module at start-up),
    which is what an operator whose config template rendered an empty value needs to see.
    """
    if raw is None:
        return "'self'"
    ancestors = " ".join(raw.split())
    if not ancestors:
        raise ValueError(
            f"{_FRAME_ANCESTORS_ENV} is set to an empty value: it names no parent origin, and "
            "an empty CSP frame-ancestors directive is a parse error that browsers discard, "
            "taking the clickjacking restriction with it. Unset it to keep the shipped "
            "'self' default, or set it to 'none' to refuse all framing."
        )
    _refuse_wildcard(ancestors.split(), _FRAME_ANCESTORS_ENV)
    return ancestors


_FRAME_ANCESTORS = _frame_ancestors(read_env_setting(_FRAME_ANCESTORS_ENV).raw)


def _cors_origins() -> list[str]:
    """Explicit allowlist, never "*"; the localhost dev fallback applies ONLY under a
    DELIBERATE local profile (shared hex-service-kit rule), so neither a secure
    deploy that forgets KB_CORS_ORIGINS nor an unconfigured run gets cross-origin trust.

    The local refusal runs FIRST, on the raw configured value, rather than on what the kit
    hands back. ``cors_allowlist`` now refuses the same wildcards itself, so on the old order
    the kit raised its own ``InsecureCorsError`` before this module's rule was ever reached and
    the policy quietly changed owner. Refusing on the way in keeps :func:`_refuse_wildcard` the
    one authority over both allowlists: a single exception type and a single message naming the
    variable an operator must fix, whether the value came from CORS or from frame-ancestors.
    The kit's check stays as an unreachable backstop, which is what a backstop should be."""
    configured = read_env_setting(_CORS_ORIGINS_ENV).value
    _refuse_wildcard(
        [origin.strip() for origin in configured.split(",") if origin.strip()], _CORS_ORIGINS_ENV
    )
    return cors_allowlist(_EXPOSURE, origins_env=_CORS_ORIGINS_ENV, dev_origins=_DEV_ORIGINS)


app = FastAPI(
    title="A2 Enterprise Knowledge Base",
    version="0.1.0",
    description=(
        "ACL-aware governed RAG over the bank corpus, on the Gemini Enterprise Agent "
        "Platform. Serves ACL-filtered, cited passages and grounded answers, and "
        "governs document ingestion (redact, parse, index, freshness/residency)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    # The dev-persona header is a relaxation of the identity seam, so it is offered only to a
    # DELIBERATE local profile, never to an unconfigured run.
    allow_headers=["Content-Type", "Authorization"]
    + (["X-Dev-Persona"] if _EXPOSURE == "local" else []),
)


# Security-header baseline (C6), from the shared commons so every service in the catalog
# emits the same set: CSP frame-ancestors (who may iframe the KB console UI), the matching
# X-Frame-Options, X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer, and
# Strict-Transport-Security on every non-local profile (TLS terminates in front of us).
add_security_headers(app, frame_ancestors=_FRAME_ANCESTORS, profile=_EXPOSURE)


# A request arrives with nothing authenticating the END USER unless BOTH of these hold, and the
# guard bounds every case where either fails:
#
#   1. a profile was chosen. Absent that, nobody selected an identity scheme; the seeded-persona
#      adapter refuses to construct and every end-user route answers 503, but /healthz would
#      still answer a stranger, and a deployment in that state has no business being reachable.
#      It is also the one case where a settings file that bound a verifying adapter must NOT buy
#      the relaxation: unset is not consent, whatever the binding says;
#   2. the identity adapter the active binding names DECLARES that it VERIFIES the end user.
#      Seeded personas arrive on the X-Dev-Persona header the caller wrote and default to a
#      persona when the header is absent, and each one carries the entitlement principals that
#      scope the ACL-aware retrieval; the on-premises placeholder resolves nobody. Neither
#      authenticates anyone, so neither may switch this off. The adapter is read from the
#      BINDING, so an adopter who wires their own IdP verifier under `onprem` lifts the bound
#      without touching this expression.
#
# Note what is NOT in this expression: KB_S2S_TOKEN. A service credential is evidence about a
# calling SERVICE and says nothing about the end-user routes, so setting one must not, and
# cannot, disable their bound. S2S routes are bounded by their own dependency.
_END_USER_AUTHENTICATED = _CHOICE.explicit and end_user_auth_kind(deps.get_settings()) == VERIFIED

# The RESTRICTION's profile string. `bind_profile` already reads an unconsented run as `local`;
# this widens the same rule to every posture that cannot authenticate an end user, so the
# start-up bound in `main()` and the request-time guard agree instead of one binding every
# interface while the other refuses every peer that reaches it.
_BIND_PROFILE = _CHOICE.bind_profile if _END_USER_AUTHENTICATED else "local"

# Registered LAST, so it is the OUTERMOST middleware: an off-loopback caller is refused before
# CORS, before the header baseline and before any route or dependency runs. Bound to the APP
# OBJECT, not to `main()`: the Dockerfile CMD is
# `python -m enterprise_kb.managed_preflight && exec uvicorn enterprise_kb.api.app:app
# --host 0.0.0.0 --port ${PORT}`, which never reaches `main()`, so a guard living only there is
# dead in every shipped process. Executed before this existed: a peer at 203.0.113.7 read the
# whole seeded-persona roster, entitlement principals included, off `GET /v1/personas`.
add_loopback_exposure_guard(
    app,
    unauthenticated=not _END_USER_AUTHENTICATED,
    insecure_demo_env="KB_ALLOW_INSECURE_DEMO",
    # The EXPOSURE profile, so a run nobody configured names itself 'unconfigured' in the
    # refusal rather than borrowing the name of a profile an operator never chose.
    posture=_EXPOSURE,
)


@app.exception_handler(RetrievalEmptyError)
async def _ungrounded_answer(request: Request, exc: RetrievalEmptyError) -> JSONResponse:
    """Refuse, in a well-formed envelope, when no permitted passage grounds the query.

    B2: the KB never returns a synthesised claim it cannot cite. The domain has already
    written the ESCALATED audit record, so the refusal is on the record before this
    response is built.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": "ungrounded",
            "detail": str(exc),
            "requires_human_review": True,
            "review_level": "enhanced",
            "citations": [],
        },
    )


# --------------------------------------------------------------------------- #
# Read surface
# --------------------------------------------------------------------------- #
@app.post(
    SEARCH_PATH,
    response_model=SearchResponse,
    tags=["retrieval"],
    dependencies=[ServiceCaller],
)
def search(
    request: SearchRequest,
    principal: CurrentPrincipal,
    service: Annotated[KnowledgeBaseService, Depends(deps.get_kb_service)],
) -> SearchResponse:
    """Return ACL-filtered, page-cited passages for a query.

    The audit actor, the entitlement principals and the tenant partition all come from
    the verified ``principal``. The request-body actor is gone, and any client-supplied
    ``acl_principals`` are entitlement-checked: they may only NARROW the verified
    principal's own entitlement to a subset, never widen it (a caller cannot read more by
    asserting a privileged group id), and the tenant is never client-supplied.
    """
    passages = service.search(
        request.query,
        actor=principal.actor,
        acl_principals=principal.entitlement_principals(request.acl_principals),
        tenant=principal.tenant,
        top_k=request.top_k,
        filters=request.filters,
    )
    return SearchResponse.from_domain(passages)


@app.post(
    ANSWER_PATH,
    response_model=AnswerResponse,
    tags=["retrieval"],
    dependencies=[ServiceCaller],
)
def answer(
    request: AnswerRequest,
    principal: CurrentPrincipal,
    service: Annotated[KnowledgeBaseService, Depends(deps.get_kb_service)],
) -> AnswerResponse:
    """Synthesise a cited, ACL-grounded answer over the caller's permitted passages.

    Identity, entitlement scope and tenant are the server-verified ``principal``'s (see
    :func:`search`); a client-supplied ``acl_principals`` may only narrow, never widen.
    """
    result = service.answer(
        request.query,
        actor=principal.actor,
        acl_principals=principal.entitlement_principals(request.acl_principals),
        tenant=principal.tenant,
        filters=request.filters,
    )
    return AnswerResponse.from_domain(result)


# --------------------------------------------------------------------------- #
# Write surface (governed ingest)
# --------------------------------------------------------------------------- #
def _require_local_write_surface(
    settings: Annotated[Settings, Depends(deps.get_settings)],
) -> None:
    """Keep managed serving identities read-only; managed writes run in the pipeline job."""
    if settings.choice.exposure_profile != "local":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Corpus writes are pipeline-only outside the local demo profile; use the "
                "reviewed refresh/ingestion workload identity."
            ),
        )


@app.post(
    "/v1/ingest",
    response_model=IngestResponse,
    tags=["ingest"],
    dependencies=[ServiceCaller, Depends(_require_local_write_surface)],
)
def ingest(
    request: IngestRequest,
    principal: CurrentPrincipal,
    service: Annotated[IngestionService, Depends(deps.get_ingestion_service)],
) -> JSONResponse | IngestResponse:
    """Redact, parse, index and record freshness/residency for a document.

    A guardrail block returns a 200 blocked envelope (flagged for review) rather than a
    5xx, so the caller always gets a well-formed, auditable response.
    """
    document = m.Document(
        id=request.document_id,
        title=request.title,
        uri=request.uri,
        source_system=_source_system(request.source_system),
        acl_tags=tuple(m.AclTag(label=t) for t in request.acl_tags),
        # The tenant partition is stamped from the verified principal, never the request
        # body, so an ingesting caller cannot plant a document into another tenant.
        tenant=principal.tenant,
        version=request.version,
    )
    try:
        content = base64.b64decode(request.content_b64)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "document_id": request.document_id,
                "detail": "content_b64 is not valid base64.",
            },
        )
    try:
        result = service.ingest(document, content, request.mime_type, actor=principal.actor)
    except GuardrailBlockedError as exc:
        return _blocked_ingest_response(request.document_id, str(exc))
    return IngestResponse.from_domain(result)


@app.delete(
    "/v1/documents/{document_id}",
    tags=["ingest"],
    dependencies=[ServiceCaller, Depends(_require_local_write_surface)],
)
def delete_document(
    document_id: str,
    principal: CurrentPrincipal,
    service: Annotated[IngestionService, Depends(deps.get_ingestion_service)],
) -> dict[str, str]:
    """Remove a document and its chunks from the governed store.

    The delete is scoped to the caller's verified ``tenant``, so a caller can never delete a
    document owned by another tenant by asserting its id (multi-tenant isolation).
    """
    service.delete(document_id, actor=principal.actor, tenant=principal.tenant)
    return {"document_id": document_id, "status": "deleted"}


def _blocked_ingest_response(document_id: str, reason: str) -> JSONResponse:
    """A 200 JSON body for a guardrail-blocked ingest request."""
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "document_id": document_id,
            "blocked": True,
            "requires_human_review": True,
            "detail": "This document was blocked by the safety guardrail and was not indexed.",
            "reason": reason or "blocked",
        },
    )


def _source_system(value: str) -> m.SourceSystem:
    try:
        return m.SourceSystem(value)
    except ValueError:
        return m.SourceSystem.OTHER


# --------------------------------------------------------------------------- #
# Corpus freshness
# --------------------------------------------------------------------------- #
@app.get(
    "/v1/corpus/status",
    response_model=CorpusStatusResponse,
    tags=["corpus"],
    dependencies=[ServiceCaller],
)
def corpus_status(principal: CurrentPrincipal) -> CorpusStatusResponse:
    """Summarise the freshness + residency ledger (requires a verified principal).

    Tenant-scoped: a caller sees only its own tenant's records plus shared/global (``""``)
    corpus, never another tenant's document metadata. An empty caller tenant (trusted local
    tooling) sees the whole ledger.
    """
    container = deps.get_container()
    records = container.ledger.all()
    if principal.tenant:
        records = [r for r in records if r.tenant in ("", principal.tenant)]
    return CorpusStatusResponse.from_records(records, container.settings.corpus.ttl_days)


# --------------------------------------------------------------------------- #
# Health & governance
# --------------------------------------------------------------------------- #
@app.get(CONTRACT_MANIFEST_PATH, tags=["governance"])
def governed_rag_contract() -> dict[str, object]:
    """Discover the owned, versioned remote contract used by sibling services."""
    return contract_manifest()


@app.get("/healthz", response_model=HealthResponse, tags=["ops"])
def healthz() -> HealthResponse:
    """Liveness/readiness probe. Reports the active profile and pinned region.

    Deliberately unauthenticated: the UI reads ``profile`` here to decide whether to show
    the local demo-persona picker (only when ``profile == 'local'``).
    """
    settings = deps.get_settings()
    return HealthResponse(status="ok", profile=settings.profile, region=settings.region)


@app.get("/v1/personas", tags=["ops"])
def personas() -> list[dict[str, str]]:
    """List seeded dev personas for the local persona picker (empty outside local profile).

    Local mode runs with no IdP; the UI uses this to let a demo/test pick an identity
    (and thus exercise per-user ACL-aware retrieval) via the ``X-Dev-Persona`` header.
    Secure profiles resolve identity from the IAP assertion, so this returns an empty list.

    So does an UNCONFIGURED run: with ``KB_PROFILE`` unset the persona adapter refuses to
    construct at all, and advertising personas the identity seam will not honour would be
    worse than advertising none.
    """
    try:
        identity = deps.get_container().identity
    except IdentityError:
        return []
    lister = getattr(identity, "personas", None)
    if lister is None:
        return []
    return [dict(p) for p in lister()]


def main() -> None:
    """Run the API locally with uvicorn; the SECOND layer of the exposure bound, not the only one.

    This refuses to BIND a non-loopback interface, which is the earlier and clearer failure, but
    it runs only when the process is started through this function. The shipped entry point is
    not: the Dockerfile CMD hands ``enterprise_kb.api.app:app`` straight to uvicorn with
    ``--host 0.0.0.0``. The bound that always applies is the ``add_loopback_exposure_guard``
    middleware registered on the app object above; do not delete it believing this covers it.
    """
    settings = deps.get_settings()
    assert_managed_profile_ready(_CHOICE.profile, settings.adapters, settings=settings)

    import uvicorn

    # Fail-closed bind (shared hex-service-kit rule): the no-auth local
    # profile binds loopback unless KB_ALLOW_INSECURE_DEMO=1 explicitly opts into exposure;
    # secure profiles keep 0.0.0.0 (container-local; ingress is fronted by the platform).
    # This is a RESTRICTION, so it keys off ``_BIND_PROFILE``, where an unconfigured run, and
    # any posture that cannot authenticate an end user, looks like ``local`` and stays confined
    # rather than inheriting the fronted 0.0.0.0 default.
    uvicorn.run(
        "enterprise_kb.api.app:app",
        host=resolve_bind_host(
            _BIND_PROFILE,
            host_env="KB_API_HOST",
            insecure_demo_env="KB_ALLOW_INSECURE_DEMO",
        ),
        port=int(os.environ.get("PORT", "8082")),
        reload=os.environ.get("KB_API_RELOAD") == "1",
    )


if __name__ == "__main__":
    main()
