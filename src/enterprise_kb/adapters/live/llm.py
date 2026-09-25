"""Live LLM adapter (LLMPort): the fleet's local open-weight model, via the shared kit client.

The ``live`` profile's core model. It delegates every call to
``hex_service_kit.localmodel.LocalModelClient``, the one client the laptop lane uses to reach
the local OpenAI-compatible server (``LOCAL_MODEL_URL`` / ``LOCAL_MODEL`` /
``LOCAL_MODEL_TIMEOUT``, read by the kit in three states). No cloud SDK and no credentials:
with web grounding off, a ``live`` run never leaves the machine.

Mapping, mirroring :class:`~enterprise_kb.adapters.gcp.gemini_llm.GeminiLLMAdapter`:

* The system instruction becomes the leading ``system`` message; ``model`` turns become
  ``assistant`` turns and every other role is user context, as the Gemini adapter folds them.
* A request carrying ``response_schema`` goes through ``complete_json``: the schema is stated
  in the prompt, the answer is validated, and a malformed first answer is fed back and retried
  by the kit. The request's temperature is passed through unchanged.
* A validated structured answer is handed on as its JSON alone (fences and prose dropped).
* ``model`` on the response is the id the server says answered, not the configured name.
* The kit reports ``usage`` as ``None`` when the server sends none (MLX does not). The domain
  :class:`LlmResponse` makes usage mandatory, so ``None`` maps to the all-zero
  :class:`TokenUsage` the type defaults to.

Failure mapping. :class:`LocalModelUnavailable` propagates, exactly as a Gemini SDK error does
from the Gemini adapter, and its message ends with the two lines that start a server. A
:class:`LocalModelOutputError` (no attempt produced schema-valid JSON) is returned as the
model's last text, because that is what a malformed Gemini answer looks like to the domain,
which parses defensively and degrades to a safe, reviewed answer rather than raising.
"""

from __future__ import annotations

import json
from typing import Any

from hex_service_kit.localmodel import (
    LocalCompletion,
    LocalModelClient,
    LocalModelOutputError,
    LocalModelSettings,
)

from ...config import Settings
from ...domain.models import LlmRequest, LlmResponse, TokenUsage

_CLASSIFY_PROMPT = (
    "Classify the text into exactly one of these labels: {labels}.\n"
    "Reply with the single label only, no punctuation or explanation.\n\n"
    "Text:\n{text}"
)


class LocalModelLLMAdapter:
    """Generate completions and triage labels on the local open-weight model."""

    def __init__(self, settings: Settings, *, client: LocalModelClient | None = None) -> None:
        self._settings = settings
        self._client = client or LocalModelClient(LocalModelSettings.from_env())

    # ------------------------------------------------------------------ #
    # LLMPort
    # ------------------------------------------------------------------ #
    def generate(self, request: LlmRequest) -> LlmResponse:
        """Generate a completion for ``request`` on the local model."""
        messages = self._to_messages(request)
        max_tokens = request.max_output_tokens
        if request.response_schema is None:
            completion = self._client.complete(
                messages, temperature=request.temperature, max_tokens=max_tokens
            )
            return self._to_response(completion)
        try:
            completion = self._client.complete_json(
                messages,
                schema=request.response_schema,
                temperature=request.temperature,
                max_tokens=max_tokens,
            )
        except LocalModelOutputError as exc:
            return LlmResponse(text=exc.last_text, model=self._client.settings.model)
        return self._to_response(completion)

    def classify(self, text: str, labels: list[str]) -> str:
        """Single-label classification on the same local model, deterministic sampling."""
        prompt = _CLASSIFY_PROMPT.format(labels=", ".join(labels), text=text)
        completion = self._client.complete(
            [{"role": "user", "content": prompt}], temperature=0.0, max_tokens=32
        )
        return _match_label(completion.text.strip(), labels)

    # ------------------------------------------------------------------ #
    # Mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_messages(request: LlmRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        for message in request.messages:
            role = "assistant" if message.role == "model" else "user"
            messages.append({"role": role, "content": message.content})
        return messages

    @staticmethod
    def _to_response(completion: LocalCompletion) -> LlmResponse:
        usage = completion.usage
        data = completion.data
        raw: dict[str, Any] | None = data if isinstance(data, dict) else None
        # A structured answer is handed on as the validated JSON alone, the shape Gemini's
        # structured output has, so a fence or a sentence around it never reaches the domain.
        text = completion.text if data is None else json.dumps(data)
        return LlmResponse(
            text=text,
            usage=usage if usage is not None else TokenUsage(),
            model=completion.model,
            raw=raw,
        )


def _match_label(raw: str, labels: list[str]) -> str:
    """Coerce the model's reply to one of ``labels`` (case-insensitive), as Gemini's does."""
    if not labels:
        return raw
    lowered = raw.lower()
    for label in labels:
        if label.lower() == lowered:
            return label
    for label in labels:
        if label.lower() in lowered:
            return label
    return labels[0]
