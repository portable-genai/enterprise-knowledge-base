"""Remote-platform registry adapter — thin HTTP client to A3.

This is an optional future client for publishing an A2A AgentCard to the shared
``agent-registry`` service. The current managed reference deployment deliberately does
not publish a card: Hrz2 has no verified Agent Runtime/A2A invocation bridge. Constructing
or retaining this portable adapter is not evidence that registration occurs. It implements
:class:`AgentRegistryPort` against the registry's ``/v1/agents`` endpoints:

* ``register`` → ``POST /v1/agents`` (``201``)
* ``get``      → ``GET  /v1/agents/{name}`` (``200`` → card, ``404`` → ``None``)
* ``list``     → ``GET  /v1/agents`` (``200`` → ``[card, ...]``)

The base URL is read from ``AGENT_REGISTRY_URL`` with a localhost default when an adopter
explicitly selects the future platform profile.
"""

from __future__ import annotations

import httpx

from ...domain.errors import KnowledgeBaseError
from ...domain.models import AgentCard, AgentSkill
from ...domain.serialization import to_jsonable
from ...envread import setting_or_default
from . import _s2s

_DEFAULT_URL = "http://localhost:8083"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class RemoteRegistryError(KnowledgeBaseError):
    """Raised when the remote registry service returns an unexpected status."""


class RemoteRegistryAdapter:
    """HTTP client for the A3 ``agent-registry`` service."""

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._base_url = _s2s.validate_base_url(
            setting_or_default("AGENT_REGISTRY_URL", _DEFAULT_URL), service="agent registry"
        )

    def register(self, card: AgentCard) -> None:
        """Publish (or upsert) this agent's card into the A3 catalog."""
        payload = to_jsonable(card)
        url = f"{self._base_url}/v1/agents"
        response = self._post(url, payload)
        if response.status_code // 100 != 2:
            self._fail(url, response)

    def get(self, name: str) -> AgentCard | None:
        """Resolve a single agent card by name; ``None`` if not registered."""
        url = f"{self._base_url}/v1/agents/{name}"
        response = self._request("GET", url)
        if response.status_code == 404:
            return None
        if response.status_code // 100 != 2:
            self._fail(url, response)
        return self._parse_card(response.json())

    def list(self) -> list[AgentCard]:
        """List every agent card currently in the A3 catalog."""
        url = f"{self._base_url}/v1/agents"
        response = self._request("GET", url)
        if response.status_code // 100 != 2:
            self._fail(url, response)
        body = response.json()
        return [self._parse_card(item) for item in (body or ())]

    # ----------------------------------------------------------------- helpers
    def _post(self, url: str, payload: dict) -> httpx.Response:
        try:
            return httpx.post(url, json=payload, timeout=_TIMEOUT, headers=_s2s.headers())
        except httpx.HTTPError as exc:
            raise RemoteRegistryError(f"registry request to {url} failed: {exc}") from exc

    def _request(self, method: str, url: str) -> httpx.Response:
        try:
            return httpx.request(method, url, timeout=_TIMEOUT, headers=_s2s.headers())
        except httpx.HTTPError as exc:
            raise RemoteRegistryError(f"registry request to {url} failed: {exc}") from exc

    @staticmethod
    def _fail(url: str, response: httpx.Response) -> None:
        raise RemoteRegistryError(
            f"registry {url} returned {response.status_code}: {response.text[:500]}"
        )

    @staticmethod
    def _parse_card(body: dict) -> AgentCard:
        skills = tuple(
            AgentSkill(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
            )
            for item in (body.get("skills") or ())
        )
        return AgentCard(
            name=str(body.get("name", "")),
            description=str(body.get("description", "")),
            url=str(body.get("url", "")),
            version=str(body.get("version", "")),
            skills=skills,
            provider=str(body.get("provider", "enterprise-knowledge-base")),
        )
