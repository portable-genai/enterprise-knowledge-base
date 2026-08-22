"""Unit tests for the IdentityPort adapters (server-side, verified identity).

The local persona adapter is the offline (no IdP/AD/LDAP) identity source used for demos
and tests; the on-prem adapter is a fail-fast placeholder. These prove the identity seam
that replaces the old client-asserted ``actor``.
"""

from __future__ import annotations

import pytest

from enterprise_kb.adapters.local.identity import LocalPersonaIdentityAdapter
from enterprise_kb.adapters.onprem.identity import OnPremIdentityAdapter
from enterprise_kb.config import Settings
from enterprise_kb.domain.identity import IdentityError, Principal, RequestContext

_SETTINGS = Settings(profile="local")


def _adapter() -> LocalPersonaIdentityAdapter:
    return LocalPersonaIdentityAdapter(_SETTINGS)


def test_default_persona_when_no_header() -> None:
    principal = _adapter().resolve(RequestContext(headers={}))
    assert principal.subject == "demo.analyst@bank.example"
    assert principal.principals  # non-empty entitlements
    assert principal.tenant == "demo-bank"
    assert principal.actor == principal.subject  # audit actor is the verified subject


def test_persona_selected_by_header() -> None:
    principal = _adapter().resolve(RequestContext(headers={"x-dev-persona": "auditor"}))
    assert principal.subject == "demo.auditor@bank.example"
    assert principal.principals == ("group:audit",)


def test_persona_header_is_case_insensitive() -> None:
    # RequestContext lower-cases lookups, and the adapter lower-cases the persona value, so
    # a host that sends `X-Dev-Persona: Other-Tenant` still resolves the cross-tenant persona.
    principal = _adapter().resolve(RequestContext(headers={"x-dev-persona": "Other-Tenant"}))
    assert principal.tenant == "other-bank"


def test_unknown_persona_raises() -> None:
    with pytest.raises(IdentityError):
        _adapter().resolve(RequestContext(headers={"x-dev-persona": "does-not-exist"}))


def test_personas_listing_for_picker() -> None:
    ids = {p["id"] for p in _adapter().personas()}
    assert {"analyst", "approver", "auditor", "other-tenant"} <= ids


def test_onprem_identity_fails_fast() -> None:
    adapter = OnPremIdentityAdapter(_SETTINGS)
    with pytest.raises(NotImplementedError):
        adapter.resolve(RequestContext(headers={}))


# --------------------------------------------------------------------------- #
# Principal.entitlement_principals : the missing entitlement check (narrow-only).
# --------------------------------------------------------------------------- #
_PRINCIPAL = Principal(subject="u", principals=("group:reader", "group:risk"))


def test_entitlement_no_request_uses_full_scope() -> None:
    assert _PRINCIPAL.entitlement_principals() == ("group:reader", "group:risk")
    assert _PRINCIPAL.entitlement_principals(()) == ("group:reader", "group:risk")


def test_entitlement_request_can_narrow_to_held_subset() -> None:
    # A legitimate scope-down hint keeps only the requested id(s) the principal holds.
    assert _PRINCIPAL.entitlement_principals(["group:reader"]) == ("group:reader",)


def test_entitlement_foreign_principal_cannot_widen() -> None:
    # Asserting a privileged group the principal does not hold never grants it: the foreign
    # id is dropped, so a client can never widen its own visibility.
    assert _PRINCIPAL.entitlement_principals(["group:admin"]) == ()
    assert _PRINCIPAL.entitlement_principals(["group:reader", "group:admin"]) == ("group:reader",)


def test_entitlement_deduplicates_and_drops_empties() -> None:
    assert _PRINCIPAL.entitlement_principals(["group:reader", "group:reader", ""]) == (
        "group:reader",
    )
