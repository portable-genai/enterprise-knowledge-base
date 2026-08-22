"""Read-only ADK tools whose identity is injected by the trusted serving boundary.

The model controls only the business query and bounded retrieval options. It never supplies an
actor, tenant or ACL principal. A :class:`VerifiedContextProvider` resolves a server-injected
``RequestContext`` through the active ``IdentityPort`` for every invocation and returns the
verified ``Principal``. Managed tool registration refuses when that provider is absent.

ADK is imported only by :func:`build_function_tools`; the callable factory and security contract
remain SDK-free and unit-testable under the local profile.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from ..config import Container, Settings, build_container
from ..domain.identity import Principal, RequestContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools import FunctionTool


class VerifiedContextUnavailable(RuntimeError):
    """Raised before registration/invocation when no verified identity context exists."""


class VerifiedContextProvider(Protocol):
    """Trusted per-invocation source of a server-verified principal."""

    def current_principal(self) -> Principal:
        """Return the current verified principal; never derive it from model arguments."""
        ...


class RequestContextPrincipalProvider:
    """Resolve a server-injected request context through an ``IdentityPort``.

    The hosting transport owns ``current_request_context``. For an HTTP service it can read the
    current request's verified headers; an Agent Runtime integration can adapt its trusted
    invocation metadata. Neither the prompt nor FunctionTool arguments can influence this seam.
    """

    def __init__(
        self,
        identity: Any,
        current_request_context: Callable[[], RequestContext],
    ) -> None:
        self._identity = identity
        self._current_request_context = current_request_context

    def current_principal(self) -> Principal:
        return self._identity.resolve(self._current_request_context())


READ_ONLY_TOOL_NAMES = ("search_kb", "answer_grounded")


def _verified_principal(provider: VerifiedContextProvider, *, managed: bool) -> Principal:
    principal = provider.current_principal()
    if not principal.subject.strip():
        raise VerifiedContextUnavailable("verified tool context has no audit subject")
    principals = principal.entitlement_principals()
    if not principals:
        raise VerifiedContextUnavailable("verified tool context has no entitlement principals")
    if managed and not principal.tenant.strip():
        raise VerifiedContextUnavailable("managed verified tool context has no tenant partition")
    return principal


def _kb_service(container: Container) -> Any:
    from ..api.deps import build_kb_service

    return build_kb_service(container)


def build_tool_callables(
    context_provider: VerifiedContextProvider,
    settings: Settings | None = None,
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Build safe callables closed over a trusted context provider and DI container."""
    chosen = settings or Settings.load()
    container = build_container(chosen)
    managed = chosen.profile in {"gcp", "platform"}

    def search_kb(query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Return permitted, cited enterprise passages for the current verified caller.

        Args:
          query: Natural-language query.
          top_k: Maximum passages after server-side tenant/tag candidate filtering.
        """
        from ..domain.serialization import to_jsonable

        principal = _verified_principal(context_provider, managed=managed)
        passages = _kb_service(container).search(
            query,
            actor=principal.actor,
            acl_principals=principal.entitlement_principals(),
            tenant=principal.tenant,
            top_k=max(1, min(top_k, 50)),
        )
        return to_jsonable(passages)

    def answer_grounded(query: str) -> dict[str, Any]:
        """Return a cited answer over evidence permitted to the current verified caller.

        Args:
          query: Natural-language question to answer from governed evidence.
        """
        from ..domain.serialization import to_jsonable

        principal = _verified_principal(context_provider, managed=managed)
        answer = _kb_service(container).answer(
            query,
            actor=principal.actor,
            acl_principals=principal.entitlement_principals(),
            tenant=principal.tenant,
        )
        return to_jsonable(answer)

    return search_kb, answer_grounded


def build_function_tools(
    context_provider: VerifiedContextProvider | None,
    settings: Settings | None = None,
) -> list[FunctionTool]:
    """Register safe ADK FunctionTools or refuse an absent verified context provider."""
    chosen = settings or Settings.load()
    from ..managed_preflight import assert_managed_agent_context_ready

    assert_managed_agent_context_ready(chosen.profile, context_provider)
    if context_provider is None:  # local/on-prem callers must still provide an explicit provider
        raise VerifiedContextUnavailable("tool registration requires a verified context provider")

    from google.adk.tools import FunctionTool

    return [FunctionTool(func=fn) for fn in build_tool_callables(context_provider, chosen)]


__all__ = [
    "READ_ONLY_TOOL_NAMES",
    "RequestContextPrincipalProvider",
    "VerifiedContextProvider",
    "VerifiedContextUnavailable",
    "build_function_tools",
    "build_tool_callables",
]
