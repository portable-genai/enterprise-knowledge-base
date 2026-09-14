"""The header a browser's assertion arrives under when this service is reached THROUGH a host.

``require_service_caller`` waives the service-to-service check for one exact case: an IAP-fronted
browser, which carries a verified end-user assertion and cannot mint a service-account OIDC
bearer token. Governed routes still require ``CurrentPrincipal``, which verifies that assertion's
signature, issuer and audience, so the waiver relaxes nothing.

**What was wrong.** The waiver looked for `x-goog-iap-jwt-assertion` and nothing else.
``x-goog-*`` is Google's reserved namespace and the serverless frontend REMOVES that whole
namespace from a request entering a service, so a browser reaching this service through an
embedding host never presents it: the host sets the reserved name, the frontend drops it, and the
host re-sends the same value as `x-portal-iap-assertion` because that name is not reserved.

So the waiver never fired for an embedded browser session. The request fell through to the
service-caller check and was refused 401 -- a browser that cannot mint a bearer token being told
to present one. It is the same defect the identity adapter carried, in a second file, and the
adapter's fix did not reach here because this is a different read.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from hex_service_kit import federation as kit_federation
from starlette.requests import Request

from enterprise_kb.api import deps, security


def _request(headers: dict[str, str]) -> Request:
    raw = [(name.lower().encode(), value.encode()) for name, value in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


@pytest.fixture()
def gcp_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """A settings object with only what ``require_service_caller`` reads off it."""

    class Choice:
        profile = "gcp"
        service_auth_configured = True

    class Settings:
        choice = Choice()

    monkeypatch.setattr(deps, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        security,
        "_authenticate_service_caller",
        lambda request: pytest.fail(
            "the browser waiver did not fire: this request was sent to the service-caller check"
        ),
    )


@pytest.mark.parametrize(
    "header",
    [kit_federation.IAP_ASSERTION_HEADER, kit_federation.PORTAL_ASSERTION_HEADER],
    ids=["edge-injected", "host-forwarded"],
)
def test_an_iap_browser_is_waived_under_either_header_name(header: str, gcp_profile: None) -> None:
    """The forwarded name is the only one an EMBEDDED browser ever presents.

    Before the fix this passed for the edge-injected name and refused for the forwarded one,
    which is exactly the population that reaches a mounted console.
    """
    security.require_service_caller(_request({header: "signed.iap.assertion"}))


@pytest.mark.parametrize(
    "header",
    [kit_federation.IAP_ASSERTION_HEADER, kit_federation.PORTAL_ASSERTION_HEADER],
    ids=["edge-injected", "host-forwarded"],
)
def test_a_blank_assertion_under_either_name_is_not_a_browser_session(
    header: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A header a hop rendered empty must not waive the service check.

    The selection function strips, so a blank value is ABSENT. Without that, a whitespace-only
    header would be truthy and would waive the one check standing between an unauthenticated
    caller and the governed data plane.
    """

    class Choice:
        profile = "gcp"
        service_auth_configured = True

    class Settings:
        choice = Choice()

    monkeypatch.setattr(deps, "get_settings", lambda: Settings())
    reached: list[bool] = []
    monkeypatch.setattr(
        security, "_authenticate_service_caller", lambda request: reached.append(True)
    )
    security.require_service_caller(_request({header: "   "}))
    assert reached == [True], "a blank assertion waived the service-to-service check"


def test_an_authorization_header_still_wins_over_the_browser_waiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A presented bearer is ALWAYS verified as S2S; the waiver cannot downgrade a bad one."""

    class Choice:
        profile = "gcp"
        service_auth_configured = True

    class Settings:
        choice = Choice()

    monkeypatch.setattr(deps, "get_settings", lambda: Settings())
    reached: list[bool] = []
    monkeypatch.setattr(
        security, "_authenticate_service_caller", lambda request: reached.append(True)
    )
    security.require_service_caller(
        _request(
            {
                "authorization": "Bearer something",
                kit_federation.PORTAL_ASSERTION_HEADER: "signed.iap.assertion",
            }
        )
    )
    assert reached == [True]


def test_no_assertion_at_all_is_refused_when_no_scheme_was_chosen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unset-profile refusal must survive the change: an absent assertion is not a waiver."""

    class Choice:
        profile = ""
        service_auth_configured = False

    class Settings:
        choice = Choice()

    monkeypatch.setattr(deps, "get_settings", lambda: Settings())
    with pytest.raises(HTTPException) as caught:
        security.require_service_caller(_request({}))
    assert caught.value.status_code == 401
