"""The runtime-control seam: what a switched-off control binds, and what a request reports.

**Disabled adapters.** When a deployment switches a cheap runtime control off (``KB_GUARDRAIL``,
``KB_PII_REDACTION``), the container binds one of these instead of the profile's class. Each
satisfies its port and does nothing, so no service grows a ``None`` branch, and the container
logs the posture once at startup.

**Request-scoped disclosure.** The API wraps the bound redaction adapter per request in
:class:`DisclosingRedaction`, so a search or answer response can tell the user when redaction
changed the query they typed. The answer path also redacts the model's draft and its critique
caveats through the same port, and masking THOSE is not a change to the user's input, so the
wrapper records which texts it changed and the route asks about the query alone.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..domain.kernel import Direction, GuardrailVerdict, RedactionResult


class DisabledGuardrail:
    """GuardrailPort with the guardrail switched off: allows everything, text unchanged."""

    enabled = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        return GuardrailVerdict(
            allowed=True, direction=direction, sanitized_text=text, reason="guardrail off"
        )


class DisabledRedaction:
    """PIIRedactionPort with redaction switched off: text unchanged, no findings."""

    enabled = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def redact(self, text: str) -> RedactionResult:
        return RedactionResult(text=text, findings=())


class DisclosingRedaction:
    """Wraps the bound redaction adapter for one request and notes which texts it changed."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._changed: set[str] = set()

    def redact(self, text: str) -> RedactionResult:
        result: RedactionResult = self._inner.redact(text)
        if result.text != text:
            self._changed.add(text)
        return result

    def changed(self, text: str) -> bool:
        """Did redaction change exactly ``text`` (the user's own input) during this request?"""
        return text in self._changed
