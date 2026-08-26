"""Gemini LLM adapter (LLMPort).

Wraps the unified **Google GenAI SDK** (``google-genai``) against the **Gemini
Enterprise Agent Platform** (Vertex backend) in ``asia-southeast1`` (Singapore).
Reasoning uses ``gemini-3.5-flash`` (thinking=high) and triage/classification
uses ``gemini-3.1-flash-lite`` — both pinned from settings; the floating ADK
default model and ``gemini-2.0-flash`` are never used.

The adapter maps the domain :class:`LlmRequest` onto
``client.models.generate_content`` (system instruction, temperature,
max-output-tokens, a :class:`ThinkingConfig` whose level is mapped from
``request.thinking``, and structured-output config when a response schema is
supplied), and maps ``usage_metadata`` back onto :class:`TokenUsage`.

All Google Cloud / GenAI SDK imports are lazy so the on-prem / test profile
imports this module without ``google-genai`` installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...config import Settings
from ...domain.models import (
    LlmRequest,
    LlmResponse,
    ThinkingLevel,
    TokenUsage,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google import genai


class GeminiLLMAdapter:
    """Generate completions and triage labels via Gemini on the Agent Platform."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._models = settings.models
        self._client: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy client construction
    # ------------------------------------------------------------------ #
    def _get_client(self) -> genai.Client:
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                vertexai=True,
                project=self._settings.project_id,
                # MODEL location, not the compute region.
                location=self._settings.models.location,
                # Gemini 3.5 Flash in Singapore is served only through Single-Zone
                # Provisioned Throughput.  Explicitly select the dedicated capacity
                # contract; falling back to shared/PayGo would fail in this region and
                # would hide a missing production prerequisite until the first request.
                http_options=types.HttpOptions(
                    api_version="v1",
                    headers={"X-Vertex-AI-LLM-Request-Type": "dedicated"},
                ),
            )
        return self._client

    # ------------------------------------------------------------------ #
    # LLMPort
    # ------------------------------------------------------------------ #
    def generate(self, request: LlmRequest) -> LlmResponse:
        """Generate a completion for ``request`` using the configured model."""
        from google.genai import types

        client = self._get_client()
        model = request.model or self._models.reasoning

        contents = self._to_contents(request)
        config = self._build_config(request, types)

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        return LlmResponse(
            text=getattr(response, "text", "") or "",
            usage=self._map_usage(getattr(response, "usage_metadata", None)),
            model=model,
        )

    def classify(self, text: str, labels: list[str]) -> str:
        """Cheap single-label classification using the triage-tier model."""
        from google.genai import types

        client = self._get_client()
        label_list = ", ".join(labels)
        prompt = (
            "Classify the text into exactly one of these labels: "
            f"{label_list}.\n"
            "Reply with the single label only, no punctuation or explanation.\n\n"
            f"Text:\n{text}"
        )

        response = client.models.generate_content(
            model=self._models.triage,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=16,
                thinking_config=types.ThinkingConfig(
                    thinking_level=self._thinking_level(ThinkingLevel.MINIMAL, types)
                ),
            ),
        )

        raw = (getattr(response, "text", "") or "").strip()
        return self._match_label(raw, labels)

    # ------------------------------------------------------------------ #
    # Request / config mapping
    # ------------------------------------------------------------------ #
    def _to_contents(self, request: LlmRequest) -> list[Any]:
        from google.genai import types

        contents: list[Any] = []
        for message in request.messages:
            # The system instruction is carried on the config, not as a turn;
            # any "system" role messages here are folded in as user context.
            role = "model" if message.role == "model" else "user"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=message.content)],
                )
            )
        return contents

    def _build_config(self, request: LlmRequest, types: Any) -> Any:
        kwargs: dict[str, Any] = {
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
            "thinking_config": types.ThinkingConfig(
                thinking_level=self._thinking_level(request.thinking, types)
            ),
        }
        if request.system_instruction:
            kwargs["system_instruction"] = request.system_instruction
        if request.response_schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = request.response_schema
        return types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _thinking_level(level: ThinkingLevel, types: Any) -> Any:
        """Map the domain :class:`ThinkingLevel` to the SDK ``ThinkingLevel``.

        Gemini 3 exposes discrete thinking levels (``LOW`` / ``HIGH``); the
        domain's finer-grained scale is collapsed onto them, with MEDIUM and
        above treated as HIGH so grounded-answer synthesis runs at full depth.
        """
        mapping = {
            ThinkingLevel.MINIMAL: types.ThinkingLevel.LOW,
            ThinkingLevel.LOW: types.ThinkingLevel.LOW,
            ThinkingLevel.MEDIUM: types.ThinkingLevel.HIGH,
            ThinkingLevel.HIGH: types.ThinkingLevel.HIGH,
        }
        return mapping.get(level, types.ThinkingLevel.HIGH)

    # ------------------------------------------------------------------ #
    # Response mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _map_usage(usage_metadata: Any) -> TokenUsage:
        if usage_metadata is None:
            return TokenUsage()
        return TokenUsage(
            input_tokens=int(getattr(usage_metadata, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage_metadata, "candidates_token_count", 0) or 0),
            thinking_tokens=int(getattr(usage_metadata, "thoughts_token_count", 0) or 0),
        )

    @staticmethod
    def _match_label(raw: str, labels: list[str]) -> str:
        """Coerce the model's reply to one of ``labels`` (case-insensitive)."""
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
