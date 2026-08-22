"""Agent Platform Sessions adapter — per-case conversation state for system A2.

Backs the domain ``SessionPort`` with the managed **Agent Platform Sessions** service
(GA), accessed through ADK's ``VertexAiSessionService``. ADK's session API is
``async``; the synchronous port methods wrap each call in ``asyncio.run`` so the rest of
the hexagon stays plain-sync.

Sessions are created with a deterministic, audit-friendly id derived from the
``user_id`` / ``case_id`` so a session can be correlated with audit and trace records.
The Vertex AI / ADK SDK import is lazy so the on-prem and test profiles import this
module without it installed.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from ...config import Settings
from ...domain.models import LlmMessage, Session


class VertexSessionsAdapter:
    """Map ADK ``VertexAiSessionService`` sessions/events to domain ``Session`` state."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app_name = settings.agent_engine.display_name or "enterprise-knowledge-base"
        # Built lazily so module import needs no ADK / Vertex AI SDK.
        self._service: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy SDK plumbing
    # ------------------------------------------------------------------ #
    def _session_service(self) -> Any:
        """Return (and cache) the ADK ``VertexAiSessionService``.

        This adapter is an optional future integration seam. No Agent Runtime resource is
        deployed or advertised by the managed reference stack; an agent-engine id is used
        only when an adopter supplies a separately reviewed resource.
        """
        if self._service is not None:
            return self._service
        from google.adk.sessions import VertexAiSessionService  # lazy

        # verify: https://google.github.io/adk-docs/sessions/session/#vertexaisessionservice
        kwargs: dict[str, Any] = {
            "project": self._settings.project_id,
            "location": self._settings.region,
        }
        resource = self._settings.agent_engine.resource_name
        if resource:
            kwargs["agent_engine_id"] = _engine_id(resource)
        self._service = VertexAiSessionService(**kwargs)
        return self._service

    # ------------------------------------------------------------------ #
    # SessionPort
    # ------------------------------------------------------------------ #
    def create_session(self, user_id: str, case_id: str | None = None) -> Session:
        """Create a managed session keyed deterministically to user/case for audit."""
        service = self._session_service()
        session_id = _audit_session_id(user_id, case_id)
        created = asyncio.run(
            service.create_session(
                app_name=self._app_name,
                user_id=user_id,
                session_id=session_id,
                state={"case_id": case_id} if case_id else None,
            )
        )
        return _to_domain_session(created, user_id=user_id, case_id=case_id)

    def get(self, session_id: str, user_id: str) -> Session | None:
        service = self._session_service()
        _assert_session_owner(session_id, user_id)
        try:
            found = asyncio.run(
                service.get_session(
                    app_name=self._app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
            )
        except Exception:  # noqa: BLE001 — absent session => None, not an error
            return None
        if found is None:
            return None
        return _to_domain_session(found, user_id=user_id, case_id=_case_from(found))

    def append(self, session_id: str, user_id: str, message: LlmMessage) -> None:
        """Append one turn to the session as an ADK ``Event``."""
        service = self._session_service()
        _assert_session_owner(session_id, user_id)
        event = _to_event(message)
        asyncio.run(
            service.append_event(
                app_name=self._app_name,
                user_id=user_id,
                session_id=session_id,
                event=event,
            )
        )

    def history(self, session_id: str, user_id: str) -> list[LlmMessage]:
        """Return the session's events mapped to domain ``LlmMessage`` turns."""
        service = self._session_service()
        _assert_session_owner(session_id, user_id)
        try:
            session = asyncio.run(
                service.get_session(
                    app_name=self._app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
            )
        except Exception:  # noqa: BLE001
            return []
        if session is None:
            return []
        events = getattr(session, "events", None) or []
        messages = (_event_to_message(e) for e in events)
        return [m for m in messages if m is not None]


# ---------------------------------------------------------------------- #
# Pure mapping helpers (no SDK types in signatures)
# ---------------------------------------------------------------------- #
def _engine_id(resource_name: str) -> str:
    """Extract the bare reasoningEngine id from a full resource path."""
    return resource_name.rsplit("/", 1)[-1] if "/" in resource_name else resource_name


def _audit_session_id(user_id: str, case_id: str | None) -> str:
    """Deterministic, collision-resistant session id keyed to user/case.

    Format ``ca-<userhash>-<casepart>`` keeps the user-id hash as the second segment so
    ``_user_from_session_id`` can recover routing without an extra lookup, while never
    leaking the raw user id into the id string (P-04, minimise PII).
    """
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    case_part = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12] if case_id else "nocase"
    return f"ca-{user_hash}-{case_part}"


def _assert_session_owner(session_id: str, user_id: str) -> None:
    """Require the verified caller identity and bind it to the deterministic session id."""
    if not user_id.strip():
        raise ValueError("managed session access requires a verified nonblank user_id")
    expected_prefix = f"ca-{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:16]}-"
    if not session_id.startswith(expected_prefix):
        raise PermissionError("session id does not belong to the verified user")


def _case_from(session: Any) -> str | None:
    state = getattr(session, "state", None)
    if isinstance(state, dict):
        value = state.get("case_id")
        return str(value) if value else None
    return None


def _to_domain_session(sdk_session: Any, *, user_id: str, case_id: str | None) -> Session:
    session_id = str(getattr(sdk_session, "id", "") or getattr(sdk_session, "name", ""))
    return Session(id=session_id, user_id=user_id, case_id=case_id)


def _to_event(message: LlmMessage) -> Any:
    """Build an ADK ``Event`` carrying the message's content and role.

    Imported lazily here (not at module top) to preserve no-SDK importability.
    """
    from google.adk.events import Event  # lazy
    from google.genai import types  # lazy

    role = "user" if message.role == "user" else "model"
    content = types.Content(role=role, parts=[types.Part(text=message.content)])
    # verify: https://google.github.io/adk-docs/events/
    return Event(author=role, content=content)


def _event_to_message(event: Any) -> LlmMessage | None:
    content = getattr(event, "content", None)
    if content is None:
        return None
    parts = getattr(content, "parts", None) or []
    text = "".join(getattr(p, "text", "") or "" for p in parts)
    if not text:
        return None
    author = getattr(event, "author", None) or getattr(content, "role", "model")
    role = "user" if str(author) == "user" else "model"
    return LlmMessage(role=role, content=text)
