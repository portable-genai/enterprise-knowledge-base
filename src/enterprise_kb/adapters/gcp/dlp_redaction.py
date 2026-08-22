"""Sensitive Data Protection (DLP) redaction adapter (A1 Guardrail Gateway).

Implements :class:`PIIRedactionPort` against **Sensitive Data Protection / DLP** of
the Gemini Enterprise Agent Platform. Every prompt and response is de-identified at
the boundary — before it reaches a model or the WORM audit sink — so PII is minimised
to the model (P-04). The call is regional (``projects/{project}/locations/{region}``)
to keep inspection inside Singapore for sovereign data residency.

The adapter builds an inline configuration that masks the DLP built-in info
types most relevant to APAC banking (names, emails, phone numbers, card numbers, IBANs)
plus the national-identifier custom info types of the CONFIGURED jurisdictions
(``pii.jurisdictions``), taken from the shared pack via
:mod:`enterprise_kb.pii_patterns` in its RE2-safe form (C4). The jurisdictions are the
same ones the local redactor and the eval ``pii_safety`` metric use, so the managed and
offline paths cannot drift apart on what counts as a national id.

The ``google.cloud.dlp_v2`` import is lazy so on-prem and test profiles load this
module with no GCP SDK installed.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ...config import Settings
from ...domain.models import RedactionFinding, RedactionResult
from ...pii_patterns import re2_custom_info_types

# Built-in info types masked when no de-identify template is configured.
_DEFAULT_INFO_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD_NUMBER",
    "IBAN_CODE",
)

_MASKING_CHAR = "#"
# DLP accepts much larger content payloads, but a request can also hit its finding-count
# ceiling long before the byte ceiling on dense financial identifiers. Eight KiB keeps
# even tightly packed practical identifiers below 3,000 findings while retaining bounded
# retry behavior for ordinary long PDFs.
_MAX_DLP_CHUNK_BYTES = 8 * 1024
_BOUNDARY_CONTEXT_CHARS = 2048


class DlpRedactionAdapter:
    """De-identify PII via DLP ``deidentify_content`` (templates or inline config)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._parent = f"projects/{settings.project_id}/locations/{settings.region}"
        # National-identifier custom info types for the configured jurisdictions (C4).
        self._custom_info_types = re2_custom_info_types(settings.pii.jurisdictions)
        # DlpServiceClient is constructed lazily on first redact() call.
        self._client: Any | None = None

    # -- public API -------------------------------------------------------- #
    def redact(self, text: str) -> RedactionResult:
        """Return de-identified text plus per-info-type finding counts."""
        if not text:
            return RedactionResult(text=text, findings=())

        client = self._service_client()
        redacted_parts: list[str] = []
        counts: Counter[str] = Counter()
        retry = self._retry_policy()
        chunks = _safe_chunks(text)
        for chunk in chunks:
            response = client.deidentify_content(
                request=self._build_request(chunk), retry=retry, timeout=45.0
            )
            redacted_parts.append(str(response.item.value))
            counts.update(
                {finding.info_type: finding.count for finding in self._summarise(response)}
            )
        # Whitespace is a safe byte/token split, but PII such as a person name or formatted phone
        # number can still span it. Reinspect context on both sides and union any additional masks
        # into the disjoint results. Character masking is length-preserving; anything else refuses
        # rather than risking a corrupt or partially redacted reconstruction.
        for index in range(1, len(chunks)):
            left_width = min(_BOUNDARY_CONTEXT_CHARS, len(chunks[index - 1]))
            right_width = min(_BOUNDARY_CONTEXT_CHARS, len(chunks[index]))
            bridge = chunks[index - 1][-left_width:] + chunks[index][:right_width]
            response = client.deidentify_content(
                request=self._build_request(bridge), retry=retry, timeout=45.0
            )
            bridge_redacted = str(response.item.value)
            if len(bridge_redacted) != len(bridge):
                raise RuntimeError("DLP boundary redaction was not length-preserving")
            if any(
                redacted not in {original, _MASKING_CHAR}
                for original, redacted in zip(bridge, bridge_redacted, strict=True)
            ):
                raise RuntimeError("DLP boundary redaction returned an unexpected transformation")
            left_overlay = bridge_redacted[:left_width]
            right_overlay = bridge_redacted[left_width:]
            previous = redacted_parts[index - 1]
            current = redacted_parts[index]
            redacted_parts[index - 1] = (
                previous[:-left_width] + _union_masks(previous[-left_width:], left_overlay)
                if left_width
                else previous
            )
            redacted_parts[index] = (
                _union_masks(current[:right_width], right_overlay) + current[right_width:]
                if right_width
                else current
            )
            counts.update(
                {finding.info_type: finding.count for finding in self._summarise(response)}
            )
        findings = tuple(
            RedactionFinding(info_type=info_type, count=count)
            for info_type, count in sorted(counts.items())
        )
        return RedactionResult(text="".join(redacted_parts), findings=findings)

    # -- client / request -------------------------------------------------- #
    def _service_client(self) -> Any:
        if self._client is None:
            from google.api_core.client_options import ClientOptions
            from google.cloud import dlp_v2  # lazy

            self._client = dlp_v2.DlpServiceClient(
                client_options=ClientOptions(
                    api_endpoint=f"dlp.{self._settings.region}.rep.googleapis.com"
                )
            )
        return self._client

    @staticmethod
    def _retry_policy() -> Any:
        """Bounded retries for transient service failures; content/config errors never retry."""
        from google.api_core import exceptions
        from google.api_core.retry import Retry, if_exception_type

        return Retry(
            predicate=if_exception_type(
                exceptions.ServiceUnavailable,
                exceptions.TooManyRequests,
                exceptions.DeadlineExceeded,
            ),
            initial=0.5,
            maximum=4.0,
            multiplier=2.0,
            deadline=30.0,
        )

    def _build_request(self, text: str) -> dict[str, Any]:
        request: dict[str, Any] = {
            "parent": self._parent,
            "item": {"value": text},
        }
        request["deidentify_config"] = self._inline_deidentify_config()
        request["inspect_config"] = self._inline_inspect_config()
        return request

    # -- inline fallback configuration ------------------------------------- #
    def _inline_inspect_config(self) -> dict[str, Any]:
        # verify: https://cloud.google.com/dlp/docs/reference/rest/v2/InspectConfig
        info_types = [{"name": name} for name in _DEFAULT_INFO_TYPES]
        return {
            "info_types": info_types,
            "custom_info_types": list(self._custom_info_types),
            "min_likelihood": "POSSIBLE",
            "include_quote": False,
        }

    def _inline_deidentify_config(self) -> dict[str, Any]:
        # Mask every detected info type (built-in + the configured national custom
        # types) with a single masking character — irreversible, no surrogate to reverse.
        # verify: https://cloud.google.com/dlp/docs/reference/rest/v2/DeidentifyConfig
        custom_names = [
            {"name": str(entry["info_type"]["name"])}  # type: ignore[index]
            for entry in self._custom_info_types
        ]
        all_info_types = [{"name": name} for name in _DEFAULT_INFO_TYPES] + custom_names
        return {
            "info_type_transformations": {
                "transformations": [
                    {
                        "info_types": all_info_types,
                        "primitive_transformation": {
                            "character_mask_config": {
                                "masking_character": _MASKING_CHAR,
                            }
                        },
                    }
                ]
            }
        }

    # -- finding summary --------------------------------------------------- #
    def _summarise(self, response: Any) -> tuple[RedactionFinding, ...]:
        # The overview's transformation_summaries report, per info type, how many
        # transformations were applied — i.e. how many instances were redacted.
        overview = getattr(response, "overview", None)
        summaries = getattr(overview, "transformation_summaries", None) or []
        findings: list[RedactionFinding] = []
        for summary in summaries:
            info_type = getattr(getattr(summary, "info_type", None), "name", "")
            if not info_type:
                continue
            count = self._transformed_count(summary)
            findings.append(RedactionFinding(info_type=info_type, count=count))
        return tuple(findings)

    @staticmethod
    def _transformed_count(summary: Any) -> int:
        # Sum the SUCCESS transformation results; default to 1 when unreported.
        total = 0
        for result in getattr(summary, "results", None) or []:
            code = getattr(result, "code", None)
            code_name = getattr(code, "name", str(code))
            if code_name == "SUCCESS":
                total += int(getattr(result, "count", 0) or 0)
        if total <= 0:
            total = int(getattr(summary, "transformed_bytes", 0) or 0) and 1 or 1
        return total


def _safe_chunks(text: str, max_bytes: int = _MAX_DLP_CHUNK_BYTES) -> tuple[str, ...]:
    """Split only after whitespace without bisecting a token or UTF-8 code point.

    DLP's synchronous content limit is smaller than many extracted PDFs. Each boundary
    character stays in exactly one chunk, so concatenation is lossless. A single token larger
    than the safety ceiling refuses: splitting it could bisect a national identifier and let
    both halves evade inspection.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not text:
        return ()
    chunks: list[str] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        used = 0
        last_boundary: int | None = None
        cursor = start
        while cursor < text_length:
            encoded_size = len(text[cursor].encode("utf-8"))
            if used + encoded_size > max_bytes:
                break
            used += encoded_size
            cursor += 1
            if text[cursor - 1].isspace():
                last_boundary = cursor
        if cursor == text_length:
            chunks.append(text[start:cursor])
            break
        if last_boundary is None or last_boundary == start:
            raise ValueError(
                "DLP redaction refuses a token larger than the safe request limit; "
                "the token cannot be split without risking identifier evasion"
            )
        chunks.append(text[start:last_boundary])
        start = last_boundary
    assert "".join(chunks) == text
    return tuple(chunks)


def _union_masks(existing: str, overlay: str) -> str:
    """Merge two same-length character-mask results without undoing an earlier redaction."""
    if len(existing) != len(overlay):
        raise RuntimeError("DLP boundary overlay length mismatch")
    return "".join(
        _MASKING_CHAR if _MASKING_CHAR in {old, new} else old
        for old, new in zip(existing, overlay, strict=True)
    )
