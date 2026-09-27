"""Guardrail verdicts allow on exactly one answer and fail closed on everything else.

Two adapters turn a remote screening answer into a :class:`GuardrailVerdict`:

* **Remote gateway** (``platform`` profile): the A1 ``agent-guardrail-gateway`` answers in
  JSON. The mapping was ``bool(body.get("allowed", False))``, so the string ``"false"``,
  ``1`` or any non-empty object ALLOWED. Only a literal JSON ``true`` may allow.
* **Model Armor** (``gcp`` profile, REST): only an aggregate ``filterMatchState`` of
  ``NO_MATCH_FOUND`` from an ``invocationResult`` of ``SUCCESS``, with no filter reporting a
  match, allows. ``MATCH_FOUND``, a missing result, ``*_UNSPECIFIED``, ``PARTIAL`` and
  ``FAILURE`` block (a skipped filter reports ``NO_MATCH_FOUND``, so an incomplete screen is
  not a pass), API errors propagate, and every call carries a deadline.

The Model Armor half is tested at two levels, as cio-advisory's
``test_model_armor_verdict_mapping.py`` does:

* **SDK-free** (always runs, including the SDK-free ``make check`` in CI): the wire JSON is
  built from ``_MirrorState`` / ``_MirrorInvocation``, stdlib ``IntEnum`` copies of the real
  members by name and number.
* **Real SDK** (runs where ``google-cloud-modelarmor`` is installed, skips otherwise): the
  responses are real ``modelarmor_v1`` messages serialised by the SDK's own JSON encoder (the
  REST wire shape), then screened through ``screen()`` with a fake HTTP client. The first of
  these tests pins the mirror to the real enums so the SDK-free half cannot drift.
"""

from __future__ import annotations

import enum
import json
from typing import Any

import httpx
import pytest

from enterprise_kb.adapters.gcp.model_armor_guardrail import ModelArmorGuardrailAdapter
from enterprise_kb.adapters.platform import remote_guardrail as remote_module
from enterprise_kb.adapters.platform.remote_guardrail import (
    RemoteGuardrailAdapter,
    RemoteGuardrailError,
)
from enterprise_kb.config import ModelArmorSettings, Settings
from enterprise_kb.domain.models import Direction

TEXT = "What is the travel reimbursement limit for Singapore staff?"
DIRECTIONS = [Direction.INPUT, Direction.OUTPUT]


# =========================================================================== #
# Remote guardrail gateway: allow only on a literal JSON ``true``
# =========================================================================== #
class _GatewayPost:
    """Stands in for ``httpx.post``: records the call and returns a canned response."""

    def __init__(self, *, body: Any = None, status: int = 200, error: Exception | None = None):
        self._body = body
        self._status = status
        self._error = error
        self.timeouts: list[Any] = []

    def __call__(self, url: str, *, json: Any, timeout: Any, headers: Any) -> httpx.Response:
        self.timeouts.append(timeout)
        if self._error is not None:
            raise self._error
        return httpx.Response(self._status, json=self._body, request=httpx.Request("POST", url))


def _gateway(monkeypatch: pytest.MonkeyPatch, post: _GatewayPost) -> RemoteGuardrailAdapter:
    monkeypatch.delenv("GUARDRAIL_GATEWAY_URL", raising=False)
    monkeypatch.delenv("S2S_TOKEN", raising=False)
    monkeypatch.delenv("S2S_SIGNING_KEY", raising=False)
    monkeypatch.setattr(remote_module.httpx, "post", post)
    return RemoteGuardrailAdapter(settings=object())


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_gateway_literal_true_allows(monkeypatch: pytest.MonkeyPatch, direction: Direction) -> None:
    body = {"allowed": True, "sanitized_text": TEXT, "reason": "clean"}
    verdict = _gateway(monkeypatch, _GatewayPost(body=body)).screen(TEXT, direction)
    assert verdict.allowed is True
    assert verdict.direction is direction


@pytest.mark.parametrize(
    "allowed",
    ["false", "False", "true", "no", "0", 1, 1.0, {"ok": True}, [True], False, None, 0, ""],
    ids=repr,
)
def test_gateway_anything_but_literal_true_blocks(
    monkeypatch: pytest.MonkeyPatch, allowed: Any
) -> None:
    """``bool()`` of these allowed the truthy ones; only JSON ``true`` is an allow."""
    body = {"allowed": allowed, "sanitized_text": TEXT}
    verdict = _gateway(monkeypatch, _GatewayPost(body=body)).screen(TEXT, Direction.INPUT)
    assert verdict.allowed is False


def test_gateway_missing_allowed_field_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"sanitized_text": TEXT, "reason": "gateway forgot the verdict"}
    verdict = _gateway(monkeypatch, _GatewayPost(body=body)).screen(TEXT, Direction.OUTPUT)
    assert verdict.allowed is False


def test_gateway_parse_is_literal_true_directly() -> None:
    """The mapping itself, without the transport: identical-looking truthy values differ."""
    parse = RemoteGuardrailAdapter._parse_verdict
    assert parse({"allowed": True}, Direction.INPUT).allowed is True
    for value in ("false", 1, {"x": 1}, "true"):
        assert parse({"allowed": value}, Direction.INPUT).allowed is False


@pytest.mark.parametrize("body", [[{"allowed": True}], "true", True, 1], ids=repr)
def test_gateway_non_object_body_raises(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    with pytest.raises(RemoteGuardrailError, match="not a verdict object"):
        _gateway(monkeypatch, _GatewayPost(body=body)).screen(TEXT, Direction.INPUT)


@pytest.mark.parametrize("status", [400, 403, 500, 503])
def test_gateway_http_error_propagates(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    post = _GatewayPost(body={"allowed": True}, status=status)
    with pytest.raises(RemoteGuardrailError, match=str(status)):
        _gateway(monkeypatch, post).screen(TEXT, Direction.INPUT)


def test_gateway_network_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    post = _GatewayPost(error=httpx.ReadTimeout("deadline exceeded"))
    with pytest.raises(RemoteGuardrailError, match="deadline exceeded"):
        _gateway(monkeypatch, post).screen(TEXT, Direction.INPUT)


def test_gateway_call_carries_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    post = _GatewayPost(body={"allowed": True})
    _gateway(monkeypatch, post).screen(TEXT, Direction.INPUT)
    (timeout,) = post.timeouts
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read is not None and 0 < timeout.read <= 30
    assert timeout.connect is not None and 0 < timeout.connect <= 30


# =========================================================================== #
# Model Armor (REST): SDK-free mirror half
# =========================================================================== #
class _MirrorState(enum.IntEnum):
    """``modelarmor_v1.FilterMatchState``'s members, by name and number."""

    FILTER_MATCH_STATE_UNSPECIFIED = 0
    NO_MATCH_FOUND = 1
    MATCH_FOUND = 2


class _MirrorInvocation(enum.IntEnum):
    """``modelarmor_v1.InvocationResult``'s members, by name and number."""

    INVOCATION_RESULT_UNSPECIFIED = 0
    SUCCESS = 1
    PARTIAL = 2
    FAILURE = 3


def _armor_settings() -> Settings:
    return Settings(
        profile="gcp",
        project_id="bank-kb-prod",
        model_armor=ModelArmorSettings(template_id="enterprise-knowledge-base-guardrail"),
    )


def _mirror_wire(
    state: _MirrorState | None,
    invocation: _MirrorInvocation | None = _MirrorInvocation.SUCCESS,
    filter_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The REST JSON Model Armor returns: enums by NAME, camelCase fields; ``None`` omits."""
    result: dict[str, Any] = {"filterResults": filter_results or {}}
    if state is not None:
        result["filterMatchState"] = state.name
    if invocation is not None:
        result["invocationResult"] = invocation.name
    return {"sanitizationResult": result}


def _map(response: Any, direction: Direction = Direction.INPUT) -> Any:
    return ModelArmorGuardrailAdapter(_armor_settings())._parse(response, direction, TEXT)


class _ArmorClient:
    """Stands in for the adapter's ``httpx.Client``; returns real ``httpx.Response`` objects."""

    def __init__(self, *, body: Any = None, status: int = 200) -> None:
        self._body = body
        self._status = status
        self.timeouts: list[Any] = []
        self.urls: list[str] = []

    def post(self, url: str, *, json: Any, headers: Any, timeout: Any) -> httpx.Response:
        self.urls.append(url)
        self.timeouts.append(timeout)
        return httpx.Response(self._status, json=self._body, request=httpx.Request("POST", url))


def _armor(client: _ArmorClient) -> ModelArmorGuardrailAdapter:
    adapter = ModelArmorGuardrailAdapter(_armor_settings())
    adapter._client = client  # skip the real client and ADC; the mapping is under test
    adapter._bearer_token = lambda: "test-token"  # type: ignore[method-assign]
    return adapter


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_armor_match_found_blocks_sdk_free(direction: Direction) -> None:
    verdict = _map(_mirror_wire(_MirrorState.MATCH_FOUND), direction)
    assert verdict.allowed is False


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_armor_no_match_success_allows_sdk_free(direction: Direction) -> None:
    verdict = _map(_mirror_wire(_MirrorState.NO_MATCH_FOUND), direction)
    assert verdict.allowed is True
    assert verdict.findings == ()
    assert verdict.sanitized_text == TEXT


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "invocation",
    [
        _MirrorInvocation.PARTIAL,
        _MirrorInvocation.FAILURE,
        _MirrorInvocation.INVOCATION_RESULT_UNSPECIFIED,
        None,
    ],
    ids=["PARTIAL", "FAILURE", "UNSPECIFIED", "absent"],
)
def test_armor_no_match_from_an_incomplete_screen_blocks_sdk_free(
    direction: Direction, invocation: _MirrorInvocation | None
) -> None:
    """A skipped filter reports no match. That is not a pass: the text was not screened."""
    verdict = _map(_mirror_wire(_MirrorState.NO_MATCH_FOUND, invocation), direction)
    assert verdict.allowed is False
    assert verdict.findings


def test_armor_exactly_one_combination_allows_sdk_free() -> None:
    allowed = [
        (state.name, invocation.name)
        for state in _MirrorState
        for invocation in _MirrorInvocation
        if _map(_mirror_wire(state, invocation)).allowed
    ]
    assert allowed == [("NO_MATCH_FOUND", "SUCCESS")]


@pytest.mark.parametrize(
    "response",
    [
        _mirror_wire(_MirrorState.FILTER_MATCH_STATE_UNSPECIFIED),
        _mirror_wire(None),
        {"sanitizationResult": None},
        {"sanitizationResult": {}},
        {},
    ],
    ids=["unspecified-state", "absent-state", "null-result", "empty-result", "missing-result"],
)
def test_armor_no_verdict_fails_closed_sdk_free(response: dict[str, Any]) -> None:
    assert _map(response).allowed is False


def test_armor_integer_enums_never_allow_sdk_free() -> None:
    """The REST API sends enum NAMES. An integer-encoded answer is not a verdict."""
    response = {
        "sanitizationResult": {
            "filterMatchState": int(_MirrorState.NO_MATCH_FOUND),
            "invocationResult": int(_MirrorInvocation.SUCCESS),
            "filterResults": {},
        }
    }
    assert _map(response).allowed is False


def test_armor_filter_level_match_blocks_despite_aggregate_no_match_sdk_free() -> None:
    filter_results = {
        "pi_and_jailbreak": {
            "piAndJailbreakFilterResult": {
                "matchState": _MirrorState.MATCH_FOUND.name,
                "confidenceLevel": "HIGH",
            }
        }
    }
    verdict = _map(_mirror_wire(_MirrorState.NO_MATCH_FOUND, filter_results=filter_results))
    assert verdict.allowed is False
    assert verdict.findings


@pytest.mark.parametrize("status", [400, 403, 429, 500, 503])
def test_armor_api_error_propagates(status: int) -> None:
    """An API failure must not turn into an allow; it reaches the caller."""
    client = _ArmorClient(body={"error": {"code": status}}, status=status)
    with pytest.raises(httpx.HTTPStatusError):
        _armor(client).screen(TEXT, Direction.INPUT)


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_armor_every_call_carries_a_deadline(direction: Direction) -> None:
    client = _ArmorClient(body=_mirror_wire(_MirrorState.NO_MATCH_FOUND))
    _armor(client).screen(TEXT, direction)
    (timeout,) = client.timeouts
    assert timeout is not None and 0 < timeout <= 60


# =========================================================================== #
# Model Armor (REST): real modelarmor_v1 messages on the wire
# =========================================================================== #
def _ma() -> Any:
    return pytest.importorskip("google.cloud.modelarmor_v1")


def _real_wire(
    direction: Direction,
    state_name: str | None,
    invocation_name: str = "SUCCESS",
    *,
    pi_match: bool = False,
) -> dict[str, Any]:
    """A real sanitize response, serialised by the SDK to the REST JSON shape.

    ``state_name=None`` leaves ``sanitization_result`` unset. ``pi_match`` adds a
    prompt-injection filter result that matched.
    """
    ma = _ma()
    cls = (
        ma.SanitizeUserPromptResponse
        if direction is Direction.INPUT
        else ma.SanitizeModelResponseResponse
    )
    if state_name is None:
        message = cls()
    else:
        filter_results = {}
        if pi_match:
            filter_results["pi_and_jailbreak"] = ma.FilterResult(
                pi_and_jailbreak_filter_result=ma.PiAndJailbreakFilterResult(
                    execution_state=ma.FilterExecutionState.EXECUTION_SUCCESS,
                    match_state=ma.FilterMatchState.MATCH_FOUND,
                    confidence_level=ma.DetectionConfidenceLevel.HIGH,
                )
            )
        message = cls(
            sanitization_result=ma.SanitizationResult(
                filter_match_state=ma.FilterMatchState[state_name],
                invocation_result=ma.InvocationResult[invocation_name],
                filter_results=filter_results,
            )
        )
    # Enum NAMES and camelCase field names: what the REST endpoint puts on the wire.
    wire: dict[str, Any] = json.loads(cls.to_json(message, use_integers_for_enums=False))
    return wire


@pytest.mark.parametrize(
    ("mirror", "real_name"),
    [(_MirrorState, "FilterMatchState"), (_MirrorInvocation, "InvocationResult")],
    ids=["FilterMatchState", "InvocationResult"],
)
def test_the_mirror_matches_the_real_enum(mirror: Any, real_name: str) -> None:
    real = getattr(_ma(), real_name)
    assert {m.name: int(m) for m in real} == {m.name: int(m) for m in mirror}


def test_the_real_wire_carries_the_fields_the_mapping_reads() -> None:
    """The SDK's own JSON puts the verdict where the adapter looks for it, as names."""
    result = _real_wire(Direction.INPUT, "MATCH_FOUND")["sanitizationResult"]
    assert result["filterMatchState"] == "MATCH_FOUND"
    assert result["invocationResult"] == "SUCCESS"


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_real_match_found_blocks(direction: Direction) -> None:
    client = _ArmorClient(body=_real_wire(direction, "MATCH_FOUND"))
    verdict = _armor(client).screen(TEXT, direction)
    assert verdict.allowed is False
    assert len(client.urls) == 1


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_real_no_match_success_allows(direction: Direction) -> None:
    client = _ArmorClient(body=_real_wire(direction, "NO_MATCH_FOUND"))
    verdict = _armor(client).screen(TEXT, direction)
    assert verdict.allowed is True
    assert verdict.sanitized_text == TEXT


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("invocation_name", ["PARTIAL", "FAILURE", "INVOCATION_RESULT_UNSPECIFIED"])
def test_real_no_match_from_an_incomplete_screen_blocks(
    direction: Direction, invocation_name: str
) -> None:
    client = _ArmorClient(body=_real_wire(direction, "NO_MATCH_FOUND", invocation_name))
    verdict = _armor(client).screen(TEXT, direction)
    assert verdict.allowed is False


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "state_name",
    [None, "FILTER_MATCH_STATE_UNSPECIFIED"],
    ids=["missing-result", "unspecified-state"],
)
def test_real_no_verdict_fails_closed(direction: Direction, state_name: str | None) -> None:
    client = _ArmorClient(body=_real_wire(direction, state_name))
    verdict = _armor(client).screen(TEXT, direction)
    assert verdict.allowed is False


def test_real_filter_level_match_blocks() -> None:
    client = _ArmorClient(body=_real_wire(Direction.INPUT, "NO_MATCH_FOUND", pi_match=True))
    verdict = _armor(client).screen(TEXT, Direction.INPUT)
    assert verdict.allowed is False


def test_real_exactly_one_combination_allows() -> None:
    ma = _ma()
    allowed = [
        (state.name, invocation.name)
        for state in ma.FilterMatchState
        for invocation in ma.InvocationResult
        if _map(_real_wire(Direction.INPUT, state.name, invocation.name)).allowed
    ]
    assert allowed == [("NO_MATCH_FOUND", "SUCCESS")]
