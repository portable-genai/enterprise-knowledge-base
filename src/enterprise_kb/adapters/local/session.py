"""Local session adapter (SessionPort) : in-process per-case conversation state.

The ``local`` profile's stand-in for **Agent Platform Sessions**: a small in-process
store of sessions and their message history, seedable and deterministic. When the
Firestore emulator is opted in (``FIRESTORE_EMULATOR_HOST`` set AND the client lib
imports), the adapter mirrors writes to it; the google client is imported lazily, only
on that branch, so the default path imports no google-cloud package.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import LlmMessage, Session
from ._emulator import firestore_emulator_active, firestore_emulator_host


class LocalSessionAdapter:
    """In-process session store (Firestore-emulator-backed when opted in)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, Session] = {}
        self._history: dict[str, list[LlmMessage]] = {}
        self._n = 0
        self._fs = None
        if firestore_emulator_active():
            self._fs = self._connect_emulator()

    def _connect_emulator(self):  # type: ignore[no-untyped-def]
        # Lazy import: only reached when FIRESTORE_EMULATOR_HOST is set and the lib imports.
        from google.cloud import firestore  # noqa: PLC0415

        return firestore.Client(
            project=self._settings.project_id or "local",
        )  # honours FIRESTORE_EMULATOR_HOST

    def create_session(self, user_id: str, case_id: str | None = None) -> Session:
        self._n += 1
        sid = f"sess-{self._n}"
        session = Session(id=sid, user_id=user_id, case_id=case_id)
        if self._fs is not None:
            self._fs.collection("sessions").document(sid).set(
                {"id": sid, "user_id": user_id, "case_id": case_id}
            )
        self._sessions[sid] = session
        self._history[sid] = []
        return session

    def get(self, session_id: str, user_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        return session if session is not None and session.user_id == user_id else None

    def append(self, session_id: str, user_id: str, message: LlmMessage) -> None:
        if self.get(session_id, user_id) is None:
            raise PermissionError("session id does not belong to the verified user")
        self._history.setdefault(session_id, []).append(message)

    def history(self, session_id: str, user_id: str) -> list[LlmMessage]:
        if self.get(session_id, user_id) is None:
            return []
        return list(self._history.get(session_id, []))

    @property
    def emulator_host(self) -> str | None:
        """The Firestore emulator host in use, or None for the in-process default."""
        return firestore_emulator_host() if self._fs is not None else None
