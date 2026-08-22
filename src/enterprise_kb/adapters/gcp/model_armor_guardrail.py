"""Model Armor guardrail adapter (A1 Guardrail Gateway, primary GCP backend).

Implements :class:`GuardrailPort` against **Model Armor**, the runtime AI-safety
service of the Gemini Enterprise Agent Platform. Inbound prompts are screened with
``:sanitizeUserPrompt`` and outbound model responses with ``:sanitizeModelResponse``
on the regional endpoint ``modelarmor.asia-southeast1.rep.googleapis.com`` so all
screening stays inside Singapore for sovereign-residency residency.

The adapter parses ``sanitizationResult.filterResults`` — the prompt-injection /
jailbreak, Sensitive Data Protection (SDP), malicious-URI and Responsible-AI (RAI)
filters — into :class:`GuardrailFinding` records and treats the request as *blocked*
when any filter reports ``MATCH_FOUND``.

All Google Cloud / auth SDK imports are lazy (inside ``__init__`` / methods) so the
on-prem and test profiles import this module with no GCP SDK installed.
"""

from __future__ import annotations

from typing import Any, TypeGuard

from ...config import Settings
from ...domain.models import (
    Direction,
    GuardrailCategory,
    GuardrailFinding,
    GuardrailVerdict,
)

_MATCH_FOUND = "MATCH_FOUND"
_NO_MATCH_FOUND = "NO_MATCH_FOUND"
_SUCCESS = "SUCCESS"

# Keep every request comfortably below Model Armor's prompt-size limits.  UTF-8 bytes
# are a conservative upper bound for token count, so 8 KiB is also below the 10k-token
# ceiling.  Adjacent windows overlap to prevent a prompt-injection phrase split at a
# transport boundary from evading inspection.
_MAX_REQUEST_BYTES = 8_000
_WINDOW_OVERLAP_CHARS = 1_024

# RAI sub-type key (as returned by Model Armor) -> domain GuardrailCategory.
_RAI_CATEGORY: dict[str, GuardrailCategory] = {
    "hate_speech": GuardrailCategory.HATE,
    "harassment": GuardrailCategory.HARASSMENT,
    "sexually_explicit": GuardrailCategory.SEXUAL,
    "dangerous": GuardrailCategory.DANGEROUS,
}


class ModelArmorGuardrailAdapter:
    """Screen prompts and responses through Model Armor's REST API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._armor = settings.model_armor
        self._project = settings.project_id
        self._region = settings.region
        # httpx and google-auth are resolved lazily on first screen() call.
        self._client: Any | None = None
        self._credentials: Any | None = None
        self._auth_request: Any | None = None

    # -- public API -------------------------------------------------------- #
    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        """Screen bounded overlapping windows; one blocked/invalid window blocks all."""
        verb = "sanitizeUserPrompt" if direction is Direction.INPUT else "sanitizeModelResponse"
        url = (
            f"https://{self._settings.model_armor_host}/v1/projects/{self._project}"
            f"/locations/{self._region}/templates/{self._armor.template_id}:{verb}"
        )
        windows = self._bounded_windows(text)
        verdicts: list[GuardrailVerdict] = []
        for window in windows:
            response = self._post(url, self._build_payload(window, direction))
            verdict = self._parse(response, direction, window)
            verdicts.append(verdict)
            if not verdict.allowed:
                return verdict

        if len(verdicts) == 1:
            return verdicts[0]

        # Reassembling independently transformed overlapping text could duplicate or
        # omit content.  DLP redaction is a separate preceding boundary; Model Armor is
        # a safety verdict here.  If it unexpectedly transforms any multi-window input,
        # fail closed rather than return an unverifiable reconstruction.
        transformed = any(
            verdict.sanitized_text not in (None, window)
            for verdict, window in zip(verdicts, windows, strict=True)
        )
        if transformed:
            finding = GuardrailFinding(
                category=GuardrailCategory.OTHER,
                confidence="high",
                detail=(
                    "Model Armor transformed a chunked payload that cannot be reassembled safely."
                ),
            )
            return GuardrailVerdict(
                allowed=False,
                direction=direction,
                findings=(finding,),
                sanitized_text=None,
                reason="Blocked because chunked sanitization changed the governed text.",
            )

        return GuardrailVerdict(
            allowed=True,
            direction=direction,
            findings=(),
            sanitized_text=text,
            reason="No blocking Model Armor filter matched in any bounded window.",
        )

    @staticmethod
    def _bounded_windows(text: str) -> list[str]:
        """Return UTF-8 byte-bounded, overlapping windows without splitting code points."""
        if len(text.encode("utf-8")) <= _MAX_REQUEST_BYTES:
            return [text]

        windows: list[str] = []
        start = 0
        while start < len(text):
            low = start + 1
            high = len(text)
            best = low
            while low <= high:
                middle = (low + high) // 2
                if len(text[start:middle].encode("utf-8")) <= _MAX_REQUEST_BYTES:
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            windows.append(text[start:best])
            if best >= len(text):
                break
            start = max(start + 1, best - _WINDOW_OVERLAP_CHARS)
        return windows

    # -- request construction ---------------------------------------------- #
    def _build_payload(self, text: str, direction: Direction) -> dict[str, Any]:
        # The request body keys the data object by direction.
        # verify: https://docs.cloud.google.com/model-armor/sanitize-prompts-responses
        if direction is Direction.INPUT:
            return {"userPromptData": {"text": text}}
        return {"modelResponseData": {"text": text}}

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._http_client()
        token = self._bearer_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = client.post(url, json=payload, headers=headers, timeout=30.0)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def _http_client(self) -> Any:
        import httpx  # lazy

        if self._client is None:
            self._client = httpx.Client()
        return self._client

    def _bearer_token(self) -> str:
        # google.auth.default() yields ADC credentials; refresh via a transport
        # request to mint a short-lived OAuth2 bearer token for the REST call.
        import google.auth  # lazy
        from google.auth.transport.requests import Request  # lazy

        if self._credentials is None:
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._auth_request = Request()
        if not self._credentials.valid:
            self._credentials.refresh(self._auth_request)
        token: str = self._credentials.token
        return token

    # -- response parsing -------------------------------------------------- #
    def _parse(
        self, response: dict[str, Any], direction: Direction, original_text: str
    ) -> GuardrailVerdict:
        result = response.get("sanitizationResult", {}) or {}
        filter_results = result.get("filterResults", {}) or {}

        findings: list[GuardrailFinding] = []
        findings.extend(self._parse_pi_jailbreak(filter_results))
        findings.extend(self._parse_sensitive_data(filter_results))
        findings.extend(self._parse_malicious_uris(filter_results))
        findings.extend(self._parse_rai(filter_results))

        # Model Armor can return PARTIAL/FAILURE even when no individual filter reports a
        # match. Treating that as clean would silently bypass the guardrail during a service
        # or filter failure. Only a complete SUCCESS with an explicit NO_MATCH_FOUND is safe.
        invocation_result = result.get("invocationResult")
        match_state = result.get("filterMatchState")
        complete = invocation_result == _SUCCESS
        allowed = complete and match_state == _NO_MATCH_FOUND and not findings
        if not complete:
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.OTHER,
                    confidence="high",
                    detail=(
                        "Model Armor did not complete successfully: "
                        f"{invocation_result or 'missing invocationResult'}."
                    ),
                )
            )
        elif match_state not in {_MATCH_FOUND, _NO_MATCH_FOUND}:
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.OTHER,
                    confidence="high",
                    detail="Model Armor returned no explicit aggregate match state.",
                )
            )

        sanitized_text = self._extract_sanitized_text(filter_results, original_text)
        reason = self._reason(allowed, findings)
        return GuardrailVerdict(
            allowed=allowed,
            direction=direction,
            findings=tuple(findings),
            sanitized_text=sanitized_text,
            reason=reason,
        )

    @staticmethod
    def _is_match(node: Any) -> TypeGuard[dict[str, Any]]:
        return isinstance(node, dict) and node.get("matchState") == _MATCH_FOUND

    def _parse_pi_jailbreak(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        node = (filter_results.get("pi_and_jailbreak") or {}).get("piAndJailbreakFilterResult")
        if not self._is_match(node):
            return []
        confidence = str(node.get("confidenceLevel", "")).lower() or "high"
        return [
            GuardrailFinding(
                category=GuardrailCategory.PROMPT_INJECTION,
                confidence=confidence,
                detail="Model Armor prompt-injection / jailbreak filter matched.",
            )
        ]

    def _parse_sensitive_data(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        inspect = (filter_results.get("sdp") or {}).get("sdpFilterResult", {}).get("inspectResult")
        if not self._is_match(inspect):
            return []
        info_types = sorted(
            {
                str(f.get("infoType", ""))
                for f in (inspect.get("findings") or [])
                if f.get("infoType")
            }
        )
        detail = (
            f"Sensitive data detected: {', '.join(info_types)}."
            if info_types
            else "Model Armor Sensitive Data Protection filter matched."
        )
        return [
            GuardrailFinding(
                category=GuardrailCategory.SENSITIVE_DATA,
                confidence="high",
                detail=detail,
            )
        ]

    def _parse_malicious_uris(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        node = (filter_results.get("malicious_uris") or {}).get("maliciousUriFilterResult")
        if not self._is_match(node):
            return []
        return [
            GuardrailFinding(
                category=GuardrailCategory.MALICIOUS_URL,
                confidence="high",
                detail="Model Armor malicious-URI filter matched.",
            )
        ]

    def _parse_rai(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        rai = (filter_results.get("rai") or {}).get("raiFilterResult")
        if not self._is_match(rai):
            return []
        sub_results = rai.get("raiFilterTypeResults", {}) or {}
        findings: list[GuardrailFinding] = []
        for key, category in _RAI_CATEGORY.items():
            sub = sub_results.get(key)
            if not self._is_match(sub):
                continue
            confidence = str(sub.get("confidenceLevel", "")).lower() or "medium"
            findings.append(
                GuardrailFinding(
                    category=category,
                    confidence=confidence,
                    detail=f"Model Armor Responsible-AI filter matched: {key}.",
                )
            )
        if not findings:
            # RAI matched but no recognised sub-type — record a generic finding.
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.OTHER,
                    confidence="medium",
                    detail="Model Armor Responsible-AI filter matched.",
                )
            )
        return findings

    def _extract_sanitized_text(
        self, filter_results: dict[str, Any], original_text: str
    ) -> str | None:
        # When SDP de-identification is configured on the template, Model Armor
        # returns the redacted text under sdp.sdpFilterResult.deidentifyResult.data.
        deidentify = (
            (filter_results.get("sdp") or {}).get("sdpFilterResult", {}).get("deidentifyResult")
        )
        if isinstance(deidentify, dict):
            data = deidentify.get("data") or {}
            text = data.get("text")
            if isinstance(text, str) and text:
                return text
        return original_text

    @staticmethod
    def _reason(allowed: bool, findings: list[GuardrailFinding]) -> str:
        if allowed:
            return "No blocking Model Armor filter matched."
        categories = ", ".join(sorted({f.category.value for f in findings}))
        return f"Blocked by Model Armor: {categories}." if categories else "Blocked."
