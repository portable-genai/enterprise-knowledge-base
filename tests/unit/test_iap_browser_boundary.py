"""The IAP browser journey and service-to-service ring remain distinct."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from enterprise_kb.api import security
from enterprise_kb.config import Settings


def _request(headers: dict[str, str]) -> Request:
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/v1/search", "headers": raw})


def test_exact_gcp_iap_browser_can_reach_principal_verification_without_sa_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security.deps, "get_settings", lambda: Settings(profile="gcp"))
    security.require_service_caller(_request({"x-goog-iap-jwt-assertion": "signed-jwt"}))


def test_blank_iap_assertion_is_not_browser_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.deps, "get_settings", lambda: Settings(profile="gcp"))
    monkeypatch.setattr(
        security,
        "_authenticate_service_caller",
        lambda request: (_ for _ in ()).throw(HTTPException(status_code=401)),
    )
    with pytest.raises(HTTPException):
        security.require_service_caller(_request({"x-goog-iap-jwt-assertion": "  "}))


def test_present_authorization_is_never_downgraded_to_browser_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security.deps, "get_settings", lambda: Settings(profile="gcp"))
    called = SimpleNamespace(value=False)

    def verify(request: Request) -> None:
        called.value = True
        raise HTTPException(status_code=401)

    monkeypatch.setattr(security, "_authenticate_service_caller", verify)
    with pytest.raises(HTTPException):
        security.require_service_caller(
            _request(
                {
                    "x-goog-iap-jwt-assertion": "signed-jwt",
                    "authorization": "Bearer invalid",
                }
            )
        )
    assert called.value is True
