"""The three refusals the older IAP adapter did not make, and the transport facts it copied.

This repository carries ``adapters/gcp/iap_identity.py``, the earlier of the two IAP adapter
shapes in this fleet. Measured against ``adapters/gcp/identity.py``, which the rest of the fleet
carries, it was weaker in three ways, and every one of them was reachable without a cloud SDK,
without a credential and without a network. Each is now a refusal, and each test below was
watched failing against the code as it shipped.

**A configuration failure reported as an authentication failure.** An unconfigured audience
raised a plain ``IdentityError``, which ``api/security.py`` answers 401 "authentication
required". An operator reads that and goes looking for a missing credential; no credential would
have helped, because the deployment can authenticate nobody until a variable is set. It was also
checked SECOND, after the assertion header, so the deployment-level failure was only ever
reported to callers who already had an assertion: the operator probing with curl and no header
got "missing IAP assertion header" and learned nothing about the real problem.

**A whitespace-only header taking a different path.** ``ctx.header(...)`` was read without
``.strip()``. A header a proxy or a deployment template rendered blank is truthy, so it skipped
the missing-header refusal entirely and was refused further down by the algorithm pin, which
reports a malformed token for what is actually an absent one.

**A missing extra crashing rather than refusing.** The lazy google-auth import sat outside any
``try``. A deployment without the ``[gcp]`` extra raised ``ModuleNotFoundError`` out of
``resolve``, past ``get_principal``, and FastAPI answered a bare 500 on every request: an empty
error page for the caller and nothing to read for the operator.

The fourth difference between the two families, the ``principals`` tuple, is deliberately NOT
changed here. This adapter grants ``user:<subject>``; the other family grants nothing at all. It
is this one that agrees with ``hex_service_kit.federation.principal_from_iap_claims``, so
"fixing" it toward the other family would remove a verified identity's own principal, which is a
behaviour change and a widening of nothing. That divergence is recorded rather than closed.
"""

from __future__ import annotations

import base64
import builtins
import json as _json

import pytest
from hex_service_kit import federation as kit_federation

from enterprise_kb.adapters.gcp.iap_identity import (
    IapAudienceUnconfiguredError,
    IapIdentityAdapter,
    IapVerifierUnavailableError,
)
from enterprise_kb.domain.identity import IdentityError, RequestContext
from enterprise_kb.ports.identity import EndUserAuthUnavailableError

_AUDIENCE = "/projects/1234567890/global/backendServices/42"


def _token(alg: str = "RS256") -> str:
    """A structurally real compact JWS. Only the header is read, and nothing is signed."""
    header = (
        base64.urlsafe_b64encode(_json.dumps({"alg": alg, "typ": "JWT"}).encode())
        .decode()
        .rstrip("=")
    )
    payload = base64.urlsafe_b64encode(b'{"sub":"1"}').decode().rstrip("=")
    return f"{header}.{payload}.c2ln"


def _adapter(audience: str = _AUDIENCE) -> IapIdentityAdapter:
    """The adapter with only its one piece of deployment configuration supplied.

    Built without touching ``Settings``: the audience is the single field ``resolve`` reads, and
    constructing the whole container would make these tests depend on every other port.
    """
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = None
    adapter._audience = audience
    # Some repositories in this family read the audience in THREE states and keep the
    # "set and deliberately emptied" flag to say so in the message. Seeded here so the
    # helper works either way; where the attribute is unused it costs nothing.
    adapter._audience_configured_empty = False
    return adapter


# --------------------------------------------------------------------------------------- #
# The transport facts.
# --------------------------------------------------------------------------------------- #
def test_the_transport_facts_are_the_commons_values() -> None:
    """The header, the issuer and the key set are REBOUND from the kit, not re-declared.

    These three strings were copied into every repository that verifies an IAP assertion. A
    local copy that drifts stays green forever, because a literal always agrees with itself, and
    the fleet only finds out when two deployments disagree about which header carries identity.
    """
    from enterprise_kb.adapters.gcp import iap_identity

    assert iap_identity._ASSERTION_HEADER == kit_federation.IAP_ASSERTION_HEADER
    assert iap_identity._IAP_ISSUER == kit_federation.IAP_ISSUER
    assert iap_identity._IAP_KEYS_URL == kit_federation.IAP_KEYS_URL


# --------------------------------------------------------------------------------------- #
# (a) A configuration failure is the deployment's, not the caller's.
# --------------------------------------------------------------------------------------- #
def test_an_unconfigured_audience_is_a_deployment_failure_with_its_own_status() -> None:
    """503, not 401, and it is an EndUserAuthUnavailableError so the API can tell them apart."""
    headers = {"x-goog-iap-jwt-assertion": _token()}
    with pytest.raises(IapAudienceUnconfiguredError) as caught:
        _adapter(audience="").resolve(RequestContext(headers=headers))
    assert caught.value.http_status == 503
    assert isinstance(caught.value, EndUserAuthUnavailableError)
    assert isinstance(caught.value, IdentityError)
    # The message names the variable, because the fix is in the deployment.
    assert "_IAP_AUDIENCE is not configured" in str(caught.value)


def test_the_audience_is_checked_before_the_header_so_a_bare_probe_learns_the_truth() -> None:
    """The ordering is the point, and it is what made this invisible.

    Checked second, an operator curling the service with no assertion header got "missing IAP
    assertion header" and went looking at the load balancer. The deployment-level failure was
    reported only to callers who already had an assertion, which is the population least likely
    to be the operator debugging it.
    """
    with pytest.raises(IapAudienceUnconfiguredError):
        _adapter(audience="").resolve(RequestContext(headers={}))


def test_a_configured_audience_still_refuses_a_caller_with_no_assertion_as_a_401() -> None:
    """The split must not swallow the ordinary case: no assertion is still the caller's problem."""
    with pytest.raises(IdentityError) as caught:
        _adapter().resolve(RequestContext(headers={}))
    assert not isinstance(caught.value, EndUserAuthUnavailableError)
    assert "missing IAP assertion header" in str(caught.value)


# --------------------------------------------------------------------------------------- #
# (b) A whitespace-only header is no header.
# --------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("blank", ["   ", "\t", "\n", " \t\n "])
def test_a_whitespace_only_assertion_header_is_an_absent_one(blank: str) -> None:
    """Not "malformed token": absent. A blank value is truthy, so it took the other path."""
    with pytest.raises(IdentityError) as caught:
        _adapter().resolve(RequestContext(headers={"x-goog-iap-jwt-assertion": blank}))
    assert "missing IAP assertion header" in str(caught.value)


# --------------------------------------------------------------------------------------- #
# (c) A missing extra refuses with a reason instead of crashing.
# --------------------------------------------------------------------------------------- #
def test_an_uninstalled_verifier_refuses_with_a_status_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The import is blocked exactly as an environment without the [gcp] extra blocks it.

    Unwrapped this was a ModuleNotFoundError, which is not an IdentityError, so it escaped
    ``get_principal`` and became a bare 500 per request.
    """
    real_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("google"):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(IapVerifierUnavailableError) as caught:
        _adapter()._verify(_token())
    assert caught.value.http_status == 503
    assert isinstance(caught.value, EndUserAuthUnavailableError)
    assert "not installed" in str(caught.value)


# --------------------------------------------------------------------------------------- #
# The API answers the two differently, which is the only reason the split is worth having.
# --------------------------------------------------------------------------------------- #
def test_the_api_answers_a_deployment_failure_with_its_own_status_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A type nothing reads is decoration. This is the read."""
    from fastapi import HTTPException
    from starlette.requests import Request

    from enterprise_kb.api import security

    class Unavailable:
        def resolve(self, ctx: RequestContext) -> object:
            raise IapAudienceUnconfiguredError("SOMETHING_IAP_AUDIENCE is not configured")

    class Container:
        identity = Unavailable()

    # Two spellings across the family: `from . import deps` and `from .deps import
    # get_container`. Patch whichever this repository actually bound.
    if hasattr(security, "deps"):
        monkeypatch.setattr(security.deps, "get_container", lambda: Container())
    else:
        monkeypatch.setattr(security, "get_container", lambda: Container())
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    with pytest.raises(HTTPException) as caught:
        security.get_principal(request)
    assert caught.value.status_code == 503
    assert "IAP_AUDIENCE is not configured" in str(caught.value.detail)


def test_the_api_still_answers_an_ordinary_refusal_with_a_bare_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the caller is told nothing they could use to forge the next attempt."""
    from fastapi import HTTPException
    from starlette.requests import Request

    from enterprise_kb.api import security

    class Refusing:
        def resolve(self, ctx: RequestContext) -> object:
            raise IdentityError("assertion header declares no 'alg'")

    class Container:
        identity = Refusing()

    # Two spellings across the family: `from . import deps` and `from .deps import
    # get_container`. Patch whichever this repository actually bound.
    if hasattr(security, "deps"):
        monkeypatch.setattr(security.deps, "get_container", lambda: Container())
    else:
        monkeypatch.setattr(security, "get_container", lambda: Container())
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    with pytest.raises(HTTPException) as caught:
        security.get_principal(request)
    assert caught.value.status_code == 401
    assert caught.value.detail == "authentication required"
